from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nimarita.config import Settings
from nimarita.domain.errors import ConflictError, NotFoundError, ValidationError
from nimarita.domain.models import DashboardState, Pair, PairInvite, PairInvitePreview, User
from nimarita.infra.links import InviteLinks, LinkBuilder
from nimarita.repositories.care import CareRepository
from nimarita.repositories.pairing import PairingRepository
from nimarita.repositories.reminders import ReminderRepository
from nimarita.repositories.users import UserRepository
from nimarita.services.audit import AuditService


@dataclass(slots=True, frozen=True)
class InviteIssueResult:
    invite: PairInvite
    raw_token: str
    links: InviteLinks


class PairingService:
    def __init__(
        self,
        *,
        pairing: PairingRepository,
        users: UserRepository,
        settings: Settings,
        links: LinkBuilder,
        reminders: ReminderRepository | None = None,
        care: CareRepository | None = None,
        audit: AuditService | None = None,
    ) -> None:
        self._pairing = pairing
        self._users = users
        self._settings = settings
        self._links = links
        self._reminders = reminders
        self._care = care
        self._audit = audit

    async def get_dashboard(self, telegram_user_id: int) -> DashboardState:
        user = await self._require_user(telegram_user_id)
        now = datetime.now(tz=UTC)
        await self._pairing.expire_due_invites(now)

        active_pair = await self._pairing.get_active_pair_for_user(user.id)
        if active_pair is not None:
            partner = await self._users.get_by_id(active_pair.partner_id_for(user.id))
            return DashboardState(
                user=user,
                active_pair=active_pair,
                partner=partner,
                outgoing_invite=None,
                incoming_invite=None,
                incoming_inviter=None,
            )

        outgoing = await self._pairing.get_latest_pending_outgoing_invite(user.id)
        incoming = await self._pairing.get_latest_pending_incoming_invite(user.id)
        incoming_inviter = None
        if incoming is not None:
            incoming_inviter = await self._users.get_by_id(incoming.inviter_user_id)

        return DashboardState(
            user=user,
            active_pair=None,
            partner=None,
            outgoing_invite=outgoing,
            incoming_invite=incoming,
            incoming_inviter=incoming_inviter,
        )

    async def create_invite(self, telegram_user_id: int) -> InviteIssueResult:
        user = await self._require_user(telegram_user_id)
        now = datetime.now(tz=UTC)
        await self._pairing.expire_due_invites(now)

        active_pair = await self._pairing.get_active_pair_for_user(user.id)
        if active_pair is not None:
            raise ConflictError("У тебя уже есть активная пара. Сначала нужно завершить её.")
        if not user.started_bot or user.private_chat_id is None:
            raise ConflictError("Сначала напиши /start боту, чтобы он мог доставлять сообщения и открыть Mini App корректно.")
        incoming = await self._pairing.get_latest_pending_incoming_invite(user.id)
        if incoming is not None:
            raise ConflictError("Сначала обработай входящее приглашение — подтверди или отклони его.")

        raw_token = secrets.token_urlsafe(24)
        invite = await self._pairing.create_invite(
            inviter_user_id=user.id,
            token_hash=_hash_token(raw_token),
            expires_at=now + timedelta(minutes=self._settings.pair_invite_ttl_minutes),
            now=now,
        )
        result = InviteIssueResult(
            invite=invite,
            raw_token=raw_token,
            links=self._links.build_invite_links(raw_token),
        )
        await self._audit_event(
            action='pair_invite_created',
            entity_id=invite.id,
            actor_user_id=user.id,
            payload={'expires_at': invite.expires_at.isoformat()},
        )
        return result

    async def preview_invite(self, telegram_user_id: int, raw_token: str) -> PairInvitePreview:
        user = await self._require_user(telegram_user_id)
        now = datetime.now(tz=UTC)
        await self._pairing.expire_due_invites(now)

        invite = await self._pairing.get_pending_invite_by_token_hash(_hash_token(raw_token))
        if invite is None:
            raise NotFoundError("Приглашение не найдено или уже недействительно.")
        active_pair = await self._pairing.get_active_pair_for_user(user.id)
        if active_pair is not None:
            raise ConflictError("У тебя уже есть активная пара. Сначала заверши её, чтобы принимать новое приглашение.")
        try:
            invite = await self._pairing.bind_pending_invite_to_user(invite.id, user.id, now)
        except LookupError as error:
            raise NotFoundError("Приглашение не найдено или уже недействительно.") from error
        except PermissionError as error:
            raise ConflictError(str(error)) from error
        except ValueError as error:
            raise ValidationError(str(error)) from error
        inviter = await self._users.get_by_id(invite.inviter_user_id)
        if inviter is None:
            raise NotFoundError("Пользователь-инициатор не найден.")
        return PairInvitePreview(invite=invite, inviter=inviter)

    async def accept_invite_by_token(self, telegram_user_id: int, raw_token: str) -> tuple[Pair, User, User]:
        preview = await self.preview_invite(telegram_user_id, raw_token)
        return await self.accept_invite_by_id(telegram_user_id, preview.invite.id)

    async def reject_invite_by_token(self, telegram_user_id: int, raw_token: str) -> tuple[PairInvite, User, User]:
        preview = await self.preview_invite(telegram_user_id, raw_token)
        return await self.reject_invite_by_id(telegram_user_id, preview.invite.id)

    async def accept_invite_by_id(self, telegram_user_id: int, invite_id: int) -> tuple[Pair, User, User]:
        user = await self._require_user(telegram_user_id)
        now = datetime.now(tz=UTC)
        await self._pairing.expire_due_invites(now)

        invite = await self._pairing.get_pending_invite_by_id(invite_id)
        if invite is None:
            raise NotFoundError("Приглашение уже недоступно.")
        if invite.inviter_user_id == user.id:
            raise ValidationError("Нельзя принять собственное приглашение.")

        try:
            accepted_invite, pair = await self._pairing.accept_invite(invite_id=invite_id, invitee_user_id=user.id, now=now)
        except LookupError as error:
            raise NotFoundError("Приглашение уже недоступно.") from error
        except PermissionError as error:
            raise ConflictError(str(error)) from error
        except ValueError as error:
            raise ConflictError(str(error)) from error

        inviter = await self._users.get_by_id(accepted_invite.inviter_user_id)
        invitee = await self._users.get_by_id(user.id)
        assert inviter is not None and invitee is not None
        await self._audit_event(
            action='pair_confirmed',
            entity_id=pair.id,
            actor_user_id=invitee.id,
            payload={'inviter_user_id': inviter.id, 'invite_id': accepted_invite.id},
        )
        return pair, inviter, invitee

    async def reject_invite_by_id(self, telegram_user_id: int, invite_id: int) -> tuple[PairInvite, User, User]:
        user = await self._require_user(telegram_user_id)
        now = datetime.now(tz=UTC)
        await self._pairing.expire_due_invites(now)

        invite = await self._pairing.get_pending_invite_by_id(invite_id)
        if invite is None:
            raise NotFoundError("Приглашение уже недоступно.")
        if invite.inviter_user_id == user.id:
            raise ValidationError("Нельзя отклонить собственное приглашение.")

        try:
            rejected_invite = await self._pairing.reject_invite(invite_id=invite_id, invitee_user_id=user.id, now=now)
        except LookupError as error:
            raise NotFoundError("Приглашение уже недоступно.") from error
        except PermissionError as error:
            raise ConflictError(str(error)) from error

        inviter = await self._users.get_by_id(rejected_invite.inviter_user_id)
        rejector = await self._users.get_by_id(user.id)
        assert inviter is not None and rejector is not None
        await self._audit_event(
            action='pair_invite_rejected',
            entity_id=rejected_invite.id,
            actor_user_id=rejector.id,
            payload={'inviter_user_id': inviter.id},
        )
        return rejected_invite, inviter, rejector

    async def unpair(self, telegram_user_id: int) -> tuple[Pair, User, User]:
        user = await self._require_user(telegram_user_id)
        now = datetime.now(tz=UTC)
        pair = await self._pairing.close_active_pair_for_user(user.id, now)
        if pair is None:
            raise NotFoundError("Активной пары нет.")
        if self._reminders is not None:
            await self._reminders.cancel_open_for_pair(pair_id=pair.id, now=now)
        if self._care is not None:
            await self._care.cancel_open_for_pair(pair_id=pair.id, now=now)
        actor = await self._users.get_by_id(user.id)
        partner = await self._users.get_by_id(pair.partner_id_for(user.id))
        assert actor is not None and partner is not None
        await self._audit_event(
            action='pair_unpaired',
            entity_id=pair.id,
            actor_user_id=actor.id,
            payload={'partner_user_id': partner.id},
        )
        return pair, actor, partner


    async def _audit_event(
        self,
        *,
        action: str,
        entity_id: str | int | None,
        actor_user_id: int | None = None,
        payload: dict | None = None,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.record(
            action=action,
            entity_type='pair',
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            payload=payload,
        )

    async def _require_user(self, telegram_user_id: int) -> User:
        user = await self._users.get_by_telegram_user_id(telegram_user_id)
        if user is None:
            raise NotFoundError("Пользователь ещё не зарегистрирован. Нажми /start в боте.")
        return user



def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

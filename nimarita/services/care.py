from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nimarita.catalog import CARE_TEMPLATE_DEFINITIONS, CareQuickReplySeed, get_quick_reply
from nimarita.config import Settings
from nimarita.domain.errors import ConflictError, NotFoundError, ValidationError
from nimarita.domain.models import CareDispatch, CareEnvelope, CareTemplate, User
from nimarita.repositories.care import CareRepository
from nimarita.repositories.pairing import PairingRepository
from nimarita.repositories.users import UserRepository
from nimarita.services.audit import AuditService

CareDeliver = Callable[[CareEnvelope], Awaitable[int]]


@dataclass(slots=True, frozen=True)
class CareReplyResult:
    envelope: CareEnvelope
    reply: CareQuickReplySeed


@dataclass(slots=True, frozen=True)
class CareDeliveryFailure:
    dispatch: CareDispatch
    sender: User
    recipient: User
    final_failure: bool


class CareService:
    def __init__(
        self,
        *,
        care: CareRepository,
        pairing: PairingRepository,
        users: UserRepository,
        settings: Settings,
        audit: AuditService | None = None,
    ) -> None:
        self._care = care
        self._pairing = pairing
        self._users = users
        self._settings = settings
        self._audit = audit
        self._seeded = False

    async def ensure_seeded(self) -> None:
        await self._ensure_seeded()

    async def list_templates(self, *, telegram_user_id: int, category: str | None = None) -> list[CareTemplate]:
        await self._ensure_seeded()
        user = await self._require_user(telegram_user_id)
        await self._require_active_pair(user)
        return await self._care.list_templates(category=category)

    async def list_history(self, *, telegram_user_id: int, limit: int = 50) -> list[CareEnvelope]:
        await self._ensure_seeded()
        user = await self._require_user(telegram_user_id)
        pair, _partner = await self._require_active_pair(user)
        return await self._build_history(pair.id, limit=limit)

    async def queue_template(self, *, telegram_user_id: int, template_code: str) -> CareEnvelope:
        await self._ensure_seeded()
        prepared = await self._prepare_dispatch(telegram_user_id=telegram_user_id, template_code=template_code)
        await self._audit_event(
            action='care_queued',
            entity_id=prepared.dispatch.id,
            actor_user_id=prepared.sender.id,
            payload={
                'pair_id': prepared.dispatch.pair_id,
                'template_code': prepared.dispatch.template_code,
                'recipient_user_id': prepared.recipient.id,
            },
        )
        return prepared

    async def send_template_now(
        self,
        *,
        telegram_user_id: int,
        template_code: str,
        deliver: CareDeliver,
    ) -> CareEnvelope:
        prepared = await self.queue_template(telegram_user_id=telegram_user_id, template_code=template_code)
        dispatch = await self._care.get_dispatch(prepared.dispatch.id)
        if dispatch is None:
            raise NotFoundError('Сообщение заботы не найдено после постановки в очередь.')
        claimed = await self._care.claim_due_dispatches(now=datetime.now(tz=UTC), limit=50)
        target = next((item for item in claimed if item.id == dispatch.id), dispatch)
        if target.status.value == 'pending':
            raise ConflictError('Сообщение заботы ещё не готово к немедленной отправке.')
        envelope = await self._build_envelope(target)
        try:
            message_id = await deliver(envelope)
        except Exception as error:
            failure = await self.mark_delivery_failure(dispatch_id=envelope.dispatch.id, error_text=str(error) or error.__class__.__name__)
            if failure.final_failure:
                raise ConflictError('Не удалось доставить сообщение заботы через Telegram.') from error
            raise ConflictError('Telegram временно недоступен. Сообщение поставлено в повторную доставку.') from error
        return await self.mark_sent(dispatch_id=envelope.dispatch.id, telegram_message_id=message_id)

    async def claim_due_dispatches(self, *, limit: int) -> list[CareEnvelope]:
        await self._ensure_seeded()
        items = await self._care.claim_due_dispatches(now=datetime.now(tz=UTC), limit=limit)
        result: list[CareEnvelope] = []
        for dispatch in items:
            try:
                result.append(await self._build_envelope(dispatch))
            except NotFoundError:
                continue
        return result

    async def mark_sent(self, *, dispatch_id: int, telegram_message_id: int) -> CareEnvelope:
        dispatch = await self._care.mark_sent(
            dispatch_id=dispatch_id,
            telegram_message_id=telegram_message_id,
            now=datetime.now(tz=UTC),
        )
        envelope = await self._build_envelope(dispatch)
        await self._audit_event(
            action='care_sent',
            entity_id=envelope.dispatch.id,
            actor_user_id=envelope.sender.id,
            payload={
                'recipient_user_id': envelope.recipient.id,
                'template_code': envelope.dispatch.template_code,
                'delivery_attempts_count': envelope.dispatch.delivery_attempts_count,
            },
        )
        return envelope

    async def mark_delivery_failure(self, *, dispatch_id: int, error_text: str) -> CareDeliveryFailure:
        current = await self._care.get_dispatch(dispatch_id)
        if current is None:
            raise NotFoundError('Сообщение заботы не найдено.')
        final_failure = current.delivery_attempts_count >= self._settings.care_max_retries
        next_attempt_at = None
        if not final_failure:
            backoff_seconds = self._settings.care_retry_base_seconds * (2 ** max(current.delivery_attempts_count - 1, 0))
            next_attempt_at = datetime.now(tz=UTC) + timedelta(seconds=backoff_seconds)
        dispatch = await self._care.mark_failed(
            dispatch_id=dispatch_id,
            error_text=error_text[:500],
            final_failure=final_failure,
            next_attempt_at_utc=next_attempt_at,
            now=datetime.now(tz=UTC),
        )
        envelope = await self._build_envelope(dispatch)
        await self._audit_event(
            action='care_failed' if final_failure else 'care_retry_scheduled',
            entity_id=envelope.dispatch.id,
            actor_user_id=envelope.sender.id,
            payload={
                'recipient_user_id': envelope.recipient.id,
                'template_code': envelope.dispatch.template_code,
                'final_failure': final_failure,
                'last_error': envelope.dispatch.last_error,
                'next_attempt_at_utc': envelope.dispatch.next_attempt_at_utc.isoformat() if envelope.dispatch.next_attempt_at_utc else None,
            },
        )
        return CareDeliveryFailure(
            dispatch=envelope.dispatch,
            sender=envelope.sender,
            recipient=envelope.recipient,
            final_failure=final_failure,
        )

    async def recover_stale_processing(self) -> int:
        recovered = await self._care.requeue_stale_processing(
            now=datetime.now(tz=UTC),
            stale_before=datetime.now(tz=UTC) - timedelta(seconds=self._settings.processing_stale_seconds),
            max_retries=self._settings.care_max_retries,
            retry_base_seconds=self._settings.care_retry_base_seconds,
        )
        if recovered:
            await self._audit_event(
                action='care_recovered_stale_processing',
                entity_id='bulk',
                payload={'count': recovered},
            )
        return recovered

    async def get_dispatch_for_recipient_action(self, *, telegram_user_id: int, dispatch_id: int) -> CareEnvelope:
        await self._ensure_seeded()
        actor = await self._require_user(telegram_user_id)
        dispatch = await self._care.get_dispatch(dispatch_id)
        if dispatch is None:
            raise NotFoundError('Сообщение заботы не найдено.')
        if dispatch.recipient_user_id != actor.id:
            raise ConflictError('Только получатель может управлять этой карточкой.')
        return await self._build_envelope(dispatch)

    async def register_quick_reply(
        self,
        *,
        telegram_user_id: int,
        dispatch_id: int,
        reply_code: str,
    ) -> CareReplyResult:
        await self._ensure_seeded()
        actor = await self._require_user(telegram_user_id)
        reply = get_quick_reply(reply_code)
        if reply is None:
            raise ValidationError('Неизвестный быстрый ответ.')
        try:
            dispatch = await self._care.register_response(
                dispatch_id=dispatch_id,
                recipient_user_id=actor.id,
                response_code=reply.code,
                response_title=reply.title,
                response_body=reply.body,
                response_emoji=reply.emoji,
                now=datetime.now(tz=UTC),
            )
        except LookupError as error:
            raise NotFoundError('Сообщение заботы не найдено.') from error
        except PermissionError as error:
            raise ConflictError(str(error)) from error
        except ValueError as error:
            raise ConflictError(str(error)) from error
        envelope = await self._build_envelope(dispatch)
        await self._audit_event(
            action='care_responded',
            entity_id=envelope.dispatch.id,
            actor_user_id=actor.id,
            payload={
                'reply_code': reply.code,
                'sender_user_id': envelope.sender.id,
            },
        )
        return CareReplyResult(envelope=envelope, reply=reply)

    async def _prepare_dispatch(self, *, telegram_user_id: int, template_code: str) -> CareEnvelope:
        user = await self._require_user(telegram_user_id)
        pair, partner = await self._require_active_pair(user)
        if partner.private_chat_id is None or not partner.started_bot:
            raise ConflictError('Партнёр ещё не готов к доставке. Он должен хотя бы один раз запустить бота.')
        template = await self._care.get_template_by_code(template_code)
        if template is None:
            raise NotFoundError('Шаблон заботы не найден.')
        await self._enforce_rate_limits(user_id=user.id, pair_id=pair.id, template_code=template_code)
        dispatch = await self._care.create_dispatch(
            pair_id=pair.id,
            sender_user_id=user.id,
            recipient_user_id=partner.id,
            template=template,
            now=datetime.now(tz=UTC),
        )
        sender = await self._users.get_by_id(dispatch.sender_user_id)
        recipient = await self._users.get_by_id(dispatch.recipient_user_id)
        if sender is None or recipient is None:
            raise NotFoundError('Не удалось восстановить участников care-сообщения.')
        return CareEnvelope(dispatch=dispatch, sender=sender, recipient=recipient)

    async def _enforce_rate_limits(self, *, user_id: int, pair_id: int, template_code: str) -> None:
        now = datetime.now(tz=UTC)
        minute_count = await self._care.count_sent_since(
            sender_user_id=user_id,
            pair_id=pair_id,
            since=now - timedelta(minutes=1),
        )
        if minute_count >= self._settings.care_per_minute_limit:
            raise ConflictError('Слишком много care-сообщений за последнюю минуту. Дай чату немного воздуха.')
        hour_count = await self._care.count_sent_since(
            sender_user_id=user_id,
            pair_id=pair_id,
            since=now - timedelta(hours=1),
        )
        if hour_count >= self._settings.care_per_hour_limit:
            raise ConflictError('Достигнут часовой лимит care-сообщений. Лучше дать переписке подышать.')
        duplicated = await self._care.has_recent_duplicate(
            sender_user_id=user_id,
            pair_id=pair_id,
            template_code=template_code,
            since=now - timedelta(minutes=self._settings.care_duplicate_window_minutes),
        )
        if duplicated:
            raise ConflictError('Этот же шаблон уже недавно отправлялся. Выбери другой или подожди немного.')

    async def _build_envelope(self, dispatch: CareDispatch) -> CareEnvelope:
        sender = await self._users.get_by_id(dispatch.sender_user_id)
        recipient = await self._users.get_by_id(dispatch.recipient_user_id)
        if sender is None or recipient is None:
            raise NotFoundError('Участники care-сообщения не найдены.')
        return CareEnvelope(dispatch=dispatch, sender=sender, recipient=recipient)

    async def _build_history(self, pair_id: int, *, limit: int) -> list[CareEnvelope]:
        dispatches = await self._care.list_history_for_pair(pair_id=pair_id, limit=limit)
        user_ids = {item.sender_user_id for item in dispatches} | {item.recipient_user_id for item in dispatches}
        cache: dict[int, User] = {}
        for user_id in user_ids:
            db_user = await self._users.get_by_id(user_id)
            if db_user is not None:
                cache[user_id] = db_user
        history: list[CareEnvelope] = []
        for dispatch in dispatches:
            sender = cache.get(dispatch.sender_user_id)
            recipient = cache.get(dispatch.recipient_user_id)
            if sender is None or recipient is None:
                continue
            history.append(CareEnvelope(dispatch=dispatch, sender=sender, recipient=recipient))
        return history

    async def _ensure_seeded(self) -> None:
        if self._seeded:
            return
        count = await self._care.count_templates()
        if count == 0:
            await self._care.seed_templates(CARE_TEMPLATE_DEFINITIONS, now=datetime.now(tz=UTC))
        self._seeded = True

    async def _require_user(self, telegram_user_id: int) -> User:
        user = await self._users.get_by_telegram_user_id(telegram_user_id)
        if user is None:
            raise NotFoundError('Пользователь ещё не зарегистрирован. Нажми /start в боте.')
        return user

    async def _require_active_pair(self, user: User):
        pair = await self._pairing.get_active_pair_for_user(user.id)
        if pair is None:
            raise ConflictError('Сначала нужна активная подтверждённая пара.')
        partner = await self._users.get_by_id(pair.partner_id_for(user.id))
        if partner is None:
            raise NotFoundError('Партнёр не найден.')
        return pair, partner

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
            entity_type='care_dispatch',
            entity_id=entity_id,
            actor_user_id=actor_user_id,
            payload=payload,
        )

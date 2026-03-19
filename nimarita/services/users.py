from __future__ import annotations

from nimarita.domain.errors import AccessDeniedError
from nimarita.domain.models import TelegramUserSnapshot, User
from nimarita.repositories.users import UserRepository
from nimarita.services.access import AccessPolicy
from nimarita.services.audit import AuditService


class UserService:
    def __init__(
        self,
        users: UserRepository,
        *,
        access: AccessPolicy | None = None,
        audit: AuditService | None = None,
    ) -> None:
        self._users = users
        self._access = access
        self._audit = audit

    async def ensure_bot_user(self, snapshot: TelegramUserSnapshot) -> User:
        await self._assert_allowed(snapshot.telegram_user_id, channel='bot')
        return await self._users.upsert_telegram_user(snapshot, started_bot=True)

    async def touch_web_user(self, snapshot: TelegramUserSnapshot) -> User:
        await self._assert_allowed(snapshot.telegram_user_id, channel='web')
        return await self._users.upsert_telegram_user(snapshot, started_bot=False)

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> User | None:
        return await self._users.get_by_telegram_user_id(telegram_user_id)

    async def is_allowed(self, telegram_user_id: int) -> bool:
        if self._access is None:
            return True
        return self._access.decide(telegram_user_id).allowed

    async def _assert_allowed(self, telegram_user_id: int, *, channel: str) -> None:
        if self._access is None:
            return
        try:
            self._access.assert_allowed(telegram_user_id)
        except AccessDeniedError:
            if self._audit is not None:
                await self._audit.record(
                    action='access_denied',
                    entity_type='access',
                    entity_id=str(telegram_user_id),
                    payload={'channel': channel, 'telegram_user_id': telegram_user_id},
                )
            raise

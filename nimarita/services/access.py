from __future__ import annotations

from dataclasses import dataclass

from nimarita.config import Settings
from nimarita.domain.errors import AccessDeniedError


@dataclass(slots=True, frozen=True)
class AccessDecision:
    telegram_user_id: int
    allowed: bool
    reason: str | None = None


class AccessPolicy:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return self._settings.access_allowlist_enabled

    def decide(self, telegram_user_id: int) -> AccessDecision:
        if not self._settings.access_allowlist_enabled:
            return AccessDecision(telegram_user_id=telegram_user_id, allowed=True)
        if telegram_user_id in self._settings.allowed_user_ids:
            return AccessDecision(telegram_user_id=telegram_user_id, allowed=True)
        return AccessDecision(
            telegram_user_id=telegram_user_id,
            allowed=False,
            reason='Сейчас бот работает в режиме ограниченного доступа. Обратись к владельцу бота, чтобы тебя добавили в allowlist.',
        )

    def assert_allowed(self, telegram_user_id: int) -> None:
        decision = self.decide(telegram_user_id)
        if decision.allowed:
            return
        raise AccessDeniedError(decision.reason or 'Доступ к боту ограничен.')

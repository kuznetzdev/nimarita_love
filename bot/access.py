from __future__ import annotations

from dataclasses import dataclass

from config import ALLOWED_CHAT_IDS, ALLOWED_USER_IDS

from .profiles import UserProfile, UserProfileRegistry, load_user_profiles


@dataclass(slots=True, frozen=True)
class AccessDeniedError(Exception):
    message: str

    def __str__(self) -> str:
        return self.message


class AccessManager:
    def __init__(
        self,
        *,
        profiles: UserProfileRegistry | None = None,
        allowed_user_ids: frozenset[int] = ALLOWED_USER_IDS,
        allowed_chat_ids: frozenset[int] = ALLOWED_CHAT_IDS,
    ) -> None:
        self._profiles = profiles or load_user_profiles()
        self._allowed_user_ids = allowed_user_ids
        self._allowed_chat_ids = allowed_chat_ids

    def ensure_allowed(self, *, user_id: int, chat_id: int) -> UserProfile | None:
        profile = self._profiles.get(user_id)
        if self._profiles.is_configured() and profile is None:
            raise AccessDeniedError(
                "Доступ закрыт: добавь пользователя в profiles.json."
            )
        if self._allowed_user_ids and user_id not in self._allowed_user_ids:
            raise AccessDeniedError(
                "Доступ закрыт: пользователь не входит в ALLOWED_USER_IDS."
            )
        if self._allowed_chat_ids and chat_id not in self._allowed_chat_ids:
            raise AccessDeniedError(
                "Доступ закрыт: чат не входит в ALLOWED_CHAT_IDS."
            )
        return profile

    def profile_for_user(self, user_id: int) -> UserProfile | None:
        return self._profiles.get(user_id)

    def available_recipients(self) -> list[dict[str, object]]:
        return self._profiles.available_recipients()

    def resolve_recipient(self, token: object) -> int | None:
        if isinstance(token, bool) or token is None:
            return None
        if isinstance(token, int):
            return token
        if isinstance(token, float):
            return int(token) if token.is_integer() else None
        if not isinstance(token, str):
            return None

        stripped = token.strip()
        if not stripped:
            return None

        try:
            return int(stripped)
        except ValueError:
            profile = self._profiles.resolve(stripped)
            if profile is None:
                return None
            return profile.id

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl

from nimarita.domain.models import TelegramUserSnapshot


@dataclass(slots=True)
class WebAuthError(Exception):
    status: int
    message: str

    def __str__(self) -> str:
        return self.message


class TelegramInitDataVerifier:
    def __init__(self, bot_token: str, ttl_seconds: int) -> None:
        self._bot_token = bot_token
        self._ttl_seconds = ttl_seconds

    def verify(self, init_data: str) -> TelegramUserSnapshot:
        if not init_data.strip():
            raise WebAuthError(status=401, message="Отсутствует Telegram initData.")

        pairs = parse_qsl(init_data, keep_blank_values=True)
        if not pairs:
            raise WebAuthError(status=401, message="Telegram initData пустой.")

        fields = {key: value for key, value in pairs}
        provided_hash = fields.pop("hash", "")
        if not provided_hash:
            raise WebAuthError(status=401, message="Отсутствует хеш Telegram initData.")

        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(fields.items(), key=lambda item: item[0])
        )
        secret_key = hmac.new(
            b"WebAppData",
            self._bot_token.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        expected_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(expected_hash, provided_hash):
            raise WebAuthError(status=401, message="Хеш Telegram initData не совпадает.")

        auth_date_text = fields.get("auth_date", "")
        try:
            auth_date = int(auth_date_text)
        except ValueError as error:
            raise WebAuthError(status=401, message="Некорректное значение Telegram auth_date.") from error

        now_timestamp = int(datetime.now(tz=UTC).timestamp())
        if auth_date > now_timestamp + 60:
            raise WebAuthError(status=401, message="Telegram auth_date указывает на будущее время.")
        if now_timestamp - auth_date > self._ttl_seconds:
            raise WebAuthError(status=401, message="Срок действия Telegram initData истёк.")

        user_payload = _parse_json_object(fields.get("user"))
        if not user_payload:
            raise WebAuthError(status=401, message="Отсутствуют данные пользователя Telegram.")

        chat_payload = _parse_json_object(fields.get("chat"))
        chat_id = _read_int(chat_payload.get("id"), default=None) if chat_payload else None

        telegram_user_id = _read_int(user_payload.get("id"), default=None)
        if telegram_user_id is None or telegram_user_id <= 0:
            raise WebAuthError(status=401, message="Некорректный идентификатор пользователя Telegram.")

        return TelegramUserSnapshot(
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            username=_read_optional_text(user_payload.get("username")),
            first_name=_read_optional_text(user_payload.get("first_name")),
            last_name=_read_optional_text(user_payload.get("last_name")),
            language_code=_read_optional_text(user_payload.get("language_code")),
        )


class SessionManager:
    def __init__(self, secret: str, ttl_seconds: int) -> None:
        self._secret = secret.encode("utf-8")
        self._ttl_seconds = ttl_seconds

    def issue(self, *, telegram_user_id: int) -> str:
        payload = {
            "sub": telegram_user_id,
            "exp": int(datetime.now(tz=UTC).timestamp()) + self._ttl_seconds,
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).rstrip(b"=")
        signature = hmac.new(self._secret, encoded, hashlib.sha256).hexdigest().encode("utf-8")
        return f"{encoded.decode('utf-8')}.{signature.decode('utf-8')}"

    def verify(self, token: str) -> int:
        if not token or "." not in token:
            raise WebAuthError(status=401, message="Отсутствует токен сессии или его формат некорректен.")
        encoded_part, signature = token.split(".", 1)
        expected_signature = hmac.new(
            self._secret,
            encoded_part.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_signature, signature):
            raise WebAuthError(status=401, message="Некорректная подпись сессии.")
        padded = encoded_part + "=" * (-len(encoded_part) % 4)
        try:
            raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
            payload = json.loads(raw.decode("utf-8"))
        except Exception as error:
            raise WebAuthError(status=401, message="Некорректный payload сессии.") from error
        exp = int(payload.get("exp", 0))
        if exp < int(datetime.now(tz=UTC).timestamp()):
            raise WebAuthError(status=401, message="Срок действия токена сессии истёк.")
        sub = int(payload.get("sub", 0))
        if sub <= 0:
            raise WebAuthError(status=401, message="Некорректный идентификатор пользователя в токене.")
        return sub



def _parse_json_object(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}



def _read_int(value: Any, default: int | None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default



def _read_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

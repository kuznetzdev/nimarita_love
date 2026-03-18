from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiohttp import web

from config import (
    BASE_DIR,
    BOT_TOKEN,
    TELEGRAM_INIT_DATA_TTL_SECONDS,
    WEBAPP_LISTEN_HOST,
    WEBAPP_LISTEN_PORT,
)

from .access import AccessDeniedError, AccessManager
from .models import ReminderKind, ReminderRecord
from .profiles import UserProfile
from .scheduler import ReminderScheduler
from .storage import ReminderStorage

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/Moscow"
FRONTEND_PATH = BASE_DIR / "webapp" / "index.html"
FALLBACK_INDEX = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Mini App</title>
  </head>
  <body>
    <p>Mini App frontend is not available on disk.</p>
  </body>
</html>
"""


@dataclass(slots=True)
class RequestError(Exception):
    status: int
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(slots=True, frozen=True)
class TelegramAuthContext:
    user_id: int
    chat_id: int
    username: str | None
    first_name: str | None
    last_name: str | None


@dataclass(slots=True, frozen=True)
class ReminderCreateRequest:
    text: str
    kind: ReminderKind
    run_at: datetime | None
    daily_time: str | None
    timezone: str
    recipient_chat_id: int | None
    voice: bool
    voice_file_id: str | None


class TelegramInitDataVerifier:
    def __init__(self, bot_token: str, ttl_seconds: int) -> None:
        self._bot_token = bot_token
        self._ttl_seconds = ttl_seconds

    def verify(self, init_data: str) -> TelegramAuthContext:
        if not init_data.strip():
            raise RequestError(status=401, message="Missing Telegram initData.")

        pairs = parse_qsl(init_data, keep_blank_values=True)
        if not pairs:
            raise RequestError(status=401, message="Telegram initData is empty.")

        fields = {key: value for key, value in pairs}
        provided_hash = fields.pop("hash", "")
        if not provided_hash:
            raise RequestError(status=401, message="Telegram initData hash is missing.")

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
            raise RequestError(status=401, message="Telegram initData hash mismatch.")

        auth_date_text = fields.get("auth_date", "")
        try:
            auth_date = int(auth_date_text)
        except ValueError as error:
            raise RequestError(status=401, message="Telegram initData auth_date is invalid.") from error

        now_timestamp = int(datetime.now(tz=UTC).timestamp())
        if auth_date > now_timestamp + 60:
            raise RequestError(status=401, message="Telegram initData auth_date is in the future.")
        if now_timestamp - auth_date > self._ttl_seconds:
            raise RequestError(status=401, message="Telegram initData has expired.")

        user_payload = _parse_json_object(fields.get("user"))
        if not user_payload:
            user_payload = _parse_json_object(fields.get("receiver"))
        if not user_payload:
            raise RequestError(status=401, message="Telegram initData user payload is missing.")

        user_id = _read_int(user_payload.get("id"))
        if user_id <= 0:
            raise RequestError(status=401, message="Telegram user id is invalid.")

        chat_payload = _parse_json_object(fields.get("chat"))
        chat_id = _read_int(chat_payload.get("id"), default=user_id) if chat_payload else user_id

        return TelegramAuthContext(
            user_id=user_id,
            chat_id=chat_id,
            username=_read_optional_text(user_payload.get("username")),
            first_name=_read_optional_text(user_payload.get("first_name")),
            last_name=_read_optional_text(user_payload.get("last_name")),
        )


@web.middleware
async def request_error_middleware(
    request: web.Request,
    handler: web.Handler,
) -> web.StreamResponse:
    try:
        return await handler(request)
    except RequestError as error:
        return web.json_response(
            {
                "ok": False,
                "error": error.message,
                "status": error.status,
            },
            status=error.status,
        )


class WebAppServer:
    def __init__(
        self,
        host: str,
        port: int,
        storage: ReminderStorage,
        scheduler: ReminderScheduler,
        *,
        bot_token: str,
        ttl_seconds: int,
        frontend_path: Path = FRONTEND_PATH,
    ) -> None:
        self._host = host
        self._port = port
        self._storage = storage
        self._scheduler = scheduler
        self._frontend_path = frontend_path
        self._verifier = TelegramInitDataVerifier(bot_token=bot_token, ttl_seconds=ttl_seconds)
        self._access = AccessManager()
        self._app = self._build_app()
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None

    @classmethod
    def from_config(
        cls,
        *,
        storage: ReminderStorage,
        scheduler: ReminderScheduler,
    ) -> "WebAppServer":
        return cls(
            host=WEBAPP_LISTEN_HOST,
            port=WEBAPP_LISTEN_PORT,
            storage=storage,
            scheduler=scheduler,
            bot_token=BOT_TOKEN,
            ttl_seconds=TELEGRAM_INIT_DATA_TTL_SECONDS,
        )

    async def start(self) -> None:
        if self._runner is not None:
            return
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self._host, port=self._port)
        await self._site.start()
        logger.info("Web app started on http://%s:%s", self._host, self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None

    def _build_app(self) -> web.Application:
        app = web.Application(middlewares=[request_error_middleware])
        app.router.add_get("/", self._index)
        app.router.add_post("/auth", self._auth)
        app.router.add_get("/reminders", self._list_reminders)
        app.router.add_post("/reminder", self._create_reminder)
        app.router.add_post("/reminder/{reminder_id:\\d+}/complete", self._complete_reminder)
        app.router.add_get("/health", self._health)
        return app

    async def _index(self, request: web.Request) -> web.Response:
        del request
        if self._frontend_path.exists():
            return web.FileResponse(self._frontend_path)
        return web.Response(text=FALLBACK_INDEX, content_type="text/html", charset="utf-8")

    async def _auth(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        auth = self._authenticate_request(request, body)
        reminders = await self._storage.list_accessible_by_chat(auth.chat_id)
        profile = self._access.profile_for_user(auth.user_id)
        return web.json_response(
            {
                "ok": True,
                "user": {
                    "id": auth.user_id,
                    "username": auth.username,
                    "first_name": auth.first_name,
                    "last_name": auth.last_name,
                },
                "profile": self._serialize_profile(profile),
                "chat": {"id": auth.chat_id},
                "is_allowed": True,
                "reminder_count": len(reminders),
                "available_recipients": self._access.available_recipients(),
            }
        )

    async def _list_reminders(self, request: web.Request) -> web.Response:
        auth = self._authenticate_request(request, {})
        reminders = await self._storage.list_accessible_by_chat(auth.chat_id)
        return web.json_response(
            {
                "ok": True,
                "reminders": [self._serialize_reminder(item) for item in reminders],
            }
        )

    async def _create_reminder(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        auth = self._authenticate_request(request, body)
        payload = self._parse_create_request(body, auth)

        if payload.kind == "daily":
            reminder = await self._storage.create_daily(
                chat_id=auth.chat_id,
                text=payload.text,
                daily_time=payload.daily_time or "09:00",
                created_at=datetime.now(tz=_resolve_timezone(payload.timezone)),
                recipient_chat_id=payload.recipient_chat_id,
                timezone=payload.timezone,
                voice=payload.voice,
                voice_file_id=payload.voice_file_id,
            )
        else:
            reminder = await self._storage.create_once(
                chat_id=auth.chat_id,
                text=payload.text,
                run_at=payload.run_at or datetime.now(tz=_resolve_timezone(payload.timezone)),
                recipient_chat_id=payload.recipient_chat_id,
                timezone=payload.timezone,
                voice=payload.voice,
                voice_file_id=payload.voice_file_id,
            )

        await self._scheduler.schedule(reminder)
        return web.json_response(
            {
                "ok": True,
                "reminder": self._serialize_reminder(reminder),
            },
            status=201,
        )

    async def _complete_reminder(self, request: web.Request) -> web.Response:
        body = await self._read_json_body(request)
        auth = self._authenticate_request(request, body)
        reminder_id = _read_int(request.match_info.get("reminder_id"))
        if reminder_id <= 0:
            raise RequestError(status=400, message="Reminder id is invalid.")

        completed_at = self._parse_completed_at(body.get("completed_at"))
        reminder = await self._storage.complete_by_id(
            reminder_id=reminder_id,
            actor_user_id=auth.user_id,
            actor_chat_id=auth.chat_id,
            now=completed_at,
        )
        if reminder is None:
            raise RequestError(status=404, message="Reminder not found or access denied.")

        if not reminder.is_active:
            self._scheduler.unschedule(reminder.reminder_id)

        return web.json_response(
            {
                "ok": True,
                "reminder": self._serialize_reminder(reminder),
            }
        )

    @staticmethod
    async def _health(request: web.Request) -> web.Response:
        del request
        return web.json_response({"status": "ok"})

    async def _read_json_body(self, request: web.Request) -> dict[str, object]:
        if not request.can_read_body:
            return {}

        raw = await request.text()
        if not raw.strip():
            return {}

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as error:
            raise RequestError(status=400, message="Request body must be valid JSON.") from error

        if not isinstance(payload, dict):
            raise RequestError(status=400, message="Request body must be a JSON object.")

        return {str(key): value for key, value in payload.items()}

    def _authenticate_request(
        self,
        request: web.Request,
        body: dict[str, object],
    ) -> TelegramAuthContext:
        init_data = self._extract_init_data(request, body)
        auth = self._verifier.verify(init_data)
        self._ensure_allowlist(auth)
        return auth

    def _extract_init_data(self, request: web.Request, body: dict[str, object]) -> str:
        header_value = request.headers.get("X-Telegram-Init-Data", "").strip()
        if header_value:
            return header_value

        query_value = request.query.get("initData", "").strip()
        if query_value:
            return query_value

        body_value = _read_optional_text(body.get("initData"))
        if body_value:
            return body_value

        raise RequestError(status=401, message="Telegram initData was not provided.")

    def _ensure_allowlist(self, auth: TelegramAuthContext) -> None:
        try:
            self._access.ensure_allowed(user_id=auth.user_id, chat_id=auth.chat_id)
        except AccessDeniedError as error:
            raise RequestError(status=403, message=str(error)) from error

    def _parse_create_request(
        self,
        body: dict[str, object],
        auth: TelegramAuthContext,
    ) -> ReminderCreateRequest:
        text = _read_optional_text(body.get("text"))
        if text is None:
            raise RequestError(status=400, message="Reminder text is required.")

        timezone_name = _read_optional_text(body.get("timezone")) or DEFAULT_TIMEZONE
        zone = _resolve_timezone(timezone_name)

        raw_kind = _read_optional_text(body.get("kind"))
        if raw_kind not in {"once", "daily"}:
            raw_kind = "daily" if _read_bool(body.get("recurring")) else "once"
        kind: ReminderKind = raw_kind

        voice = _read_bool(body.get("voice"))
        voice_file_id = (
            _read_optional_text(body.get("voice_file_id"))
            or _read_optional_text(body.get("voice_file"))
            or _read_optional_text(body.get("mediaFileId"))
        )
        recipient_chat_id = self._extract_recipient_chat_id(body, auth.chat_id)

        if kind == "daily":
            daily_time = self._parse_clock_text(
                _read_optional_text(body.get("daily_time"))
                or _read_optional_text(body.get("time"))
            )
            return ReminderCreateRequest(
                text=text,
                kind=kind,
                run_at=None,
                daily_time=daily_time,
                timezone=zone.key,
                recipient_chat_id=recipient_chat_id,
                voice=voice,
                voice_file_id=voice_file_id,
            )

        return ReminderCreateRequest(
            text=text,
            kind=kind,
            run_at=self._parse_run_at(body, zone),
            daily_time=None,
            timezone=zone.key,
            recipient_chat_id=recipient_chat_id,
            voice=voice,
            voice_file_id=voice_file_id,
        )

    def _parse_run_at(self, body: dict[str, object], zone: ZoneInfo) -> datetime:
        run_at_text = _read_optional_text(body.get("run_at"))
        if run_at_text is not None:
            parsed = _parse_datetime_text(run_at_text, zone)
            if parsed is None:
                raise RequestError(status=400, message="run_at must be ISO-8601 or DD.MM.YYYY HH:MM.")
            return parsed

        datetime_text = _read_optional_text(body.get("datetime"))
        if datetime_text is not None:
            parsed = _parse_datetime_text(datetime_text, zone)
            if parsed is None:
                raise RequestError(status=400, message="datetime must be DD.MM.YYYY HH:MM.")
            return parsed

        date_text = _read_optional_text(body.get("date"))
        time_text = _read_optional_text(body.get("time"))
        if date_text is not None and time_text is not None:
            try:
                parsed_date = datetime.strptime(date_text, "%Y-%m-%d").date()
            except ValueError as error:
                raise RequestError(status=400, message="date must be in YYYY-MM-DD format.") from error
            parsed_time = self._parse_clock_value(time_text)
            return datetime.combine(parsed_date, parsed_time, tzinfo=zone)

        if time_text is not None:
            parsed_time = self._parse_clock_value(time_text)
            return _next_occurrence(parsed_time, zone)

        raise RequestError(status=400, message="One-time reminder requires run_at, datetime, or time.")

    def _parse_completed_at(self, value: object) -> datetime:
        text = _read_optional_text(value)
        if text is None:
            return datetime.now(tz=UTC)
        parsed = _parse_datetime_text(text, ZoneInfo(DEFAULT_TIMEZONE))
        if parsed is None:
            raise RequestError(status=400, message="completed_at must be ISO-8601 or DD.MM.YYYY HH:MM.")
        return parsed

    def _parse_clock_text(self, value: str | None) -> str:
        if value is None:
            raise RequestError(status=400, message="time is required.")
        parsed = self._parse_clock_value(value)
        return parsed.strftime("%H:%M")

    @staticmethod
    def _parse_clock_value(value: str) -> time:
        try:
            parsed = datetime.strptime(value, "%H:%M")
        except ValueError as error:
            raise RequestError(status=400, message="time must be in HH:MM format.") from error
        return parsed.time().replace(second=0, microsecond=0)

    @staticmethod
    def _serialize_profile(profile: UserProfile | None) -> dict[str, object] | None:
        if profile is None:
            return None
        return profile.to_public_dict()

    def _extract_recipient_chat_id(self, body: dict[str, object], default_chat_id: int) -> int:
        for key in ("recipient_chat_id", "receiverUserId", "recipient"):
            resolved = self._access.resolve_recipient(body.get(key))
            if resolved is not None:
                return resolved
        return default_chat_id

    @staticmethod
    def _serialize_reminder(reminder: ReminderRecord) -> dict[str, object]:
        zone = _resolve_timezone(reminder.timezone)
        run_at_local = reminder.run_at.astimezone(zone) if reminder.run_at is not None else None
        return {
            "id": reminder.reminder_id,
            "text": reminder.text,
            "kind": reminder.kind,
            "recurring": reminder.recurring,
            "time": reminder.daily_time or (run_at_local.strftime("%H:%M") if run_at_local else None),
            "run_at": reminder.run_at.isoformat() if reminder.run_at is not None else None,
            "daily_time": reminder.daily_time,
            "timezone": reminder.timezone,
            "voice": reminder.voice,
            "voice_file": reminder.voice_file_id,
            "voice_file_id": reminder.voice_file_id,
            "mediaFileId": reminder.voice_file_id,
            "receiverUserId": reminder.recipient_chat_id,
            "recipient_chat_id": reminder.recipient_chat_id,
            "created_at": reminder.created_at.isoformat(),
            "last_completed_at": (
                reminder.last_completed_at.isoformat()
                if reminder.last_completed_at is not None
                else None
            ),
            "is_active": reminder.is_active,
        }


def _parse_json_object(value: object) -> dict[str, object]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): field_value for key, field_value in payload.items()}


def _resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone '%s', falling back to %s.", name, DEFAULT_TIMEZONE)
        return ZoneInfo(DEFAULT_TIMEZONE)


def _parse_datetime_text(value: str, zone: ZoneInfo) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.strptime(value, "%d.%m.%Y %H:%M")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=zone)
    return parsed.astimezone(zone)


def _next_occurrence(target_time: time, zone: ZoneInfo) -> datetime:
    now = datetime.now(tz=zone)
    candidate = datetime.combine(date.today(), target_time, tzinfo=zone)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def _read_optional_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _read_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    text = _read_optional_text(value)
    if text is None:
        return default
    try:
        return int(text)
    except ValueError:
        return default


def _read_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    text = _read_optional_text(value)
    if text is None:
        return False
    return text.lower() in {"1", "true", "yes", "y", "on"}

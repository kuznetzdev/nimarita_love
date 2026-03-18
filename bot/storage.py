from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import ReminderKind, ReminderRecord

logger = logging.getLogger(__name__)

DEFAULT_TIMEZONE = "Europe/Moscow"


class ReminderStorage:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[int, ReminderRecord] = {}
        self._lock = asyncio.Lock()
        self._next_id = 1
        self._loaded = False

    async def init_schema(self) -> None:
        async with self._lock:
            await self._load_locked()

    async def create_once(
        self,
        chat_id: int,
        text: str,
        run_at: datetime,
        *,
        recipient_chat_id: int | None = None,
        timezone: str = DEFAULT_TIMEZONE,
        voice: bool = False,
        voice_file_id: str | None = None,
    ) -> ReminderRecord:
        return await self.create(
            chat_id=chat_id,
            text=text,
            kind="once",
            recipient_chat_id=recipient_chat_id,
            timezone=timezone,
            recurring=False,
            run_at=run_at,
            daily_time=None,
            voice=voice,
            voice_file_id=voice_file_id,
        )

    async def create_daily(
        self,
        chat_id: int,
        text: str,
        daily_time: str,
        created_at: datetime,
        *,
        recipient_chat_id: int | None = None,
        timezone: str = DEFAULT_TIMEZONE,
        voice: bool = False,
        voice_file_id: str | None = None,
    ) -> ReminderRecord:
        return await self.create(
            chat_id=chat_id,
            text=text,
            kind="daily",
            recipient_chat_id=recipient_chat_id,
            timezone=timezone,
            recurring=True,
            run_at=None,
            daily_time=daily_time,
            voice=voice,
            voice_file_id=voice_file_id,
            created_at=created_at,
        )

    async def list_active_by_chat(self, chat_id: int) -> list[ReminderRecord]:
        async with self._lock:
            await self._load_locked()
            return self._sorted_records(
                reminder
                for reminder in self._records.values()
                if reminder.is_active and reminder.chat_id == chat_id
            )

    async def list_active_by_recipient_chat(self, chat_id: int) -> list[ReminderRecord]:
        async with self._lock:
            await self._load_locked()
            return self._sorted_records(
                reminder
                for reminder in self._records.values()
                if reminder.is_active and reminder.recipient_chat_id == chat_id
            )

    async def list_accessible_by_chat(self, chat_id: int) -> list[ReminderRecord]:
        async with self._lock:
            await self._load_locked()
            return self._sorted_records(
                reminder
                for reminder in self._records.values()
                if reminder.is_active and chat_id in {reminder.chat_id, reminder.recipient_chat_id}
            )

    async def list_all_active(self) -> list[ReminderRecord]:
        async with self._lock:
            await self._load_locked()
            return self._sorted_records(
                reminder for reminder in self._records.values() if reminder.is_active
            )

    async def get_active_by_id(
        self,
        reminder_id: int,
        *,
        actor_chat_id: int | None = None,
    ) -> ReminderRecord | None:
        async with self._lock:
            await self._load_locked()
            reminder = self._records.get(reminder_id)

        if reminder is None or not reminder.is_active:
            return None
        if actor_chat_id is not None and actor_chat_id not in {
            reminder.chat_id,
            reminder.recipient_chat_id,
        }:
            return None
        return reminder

    async def complete_by_id(
        self,
        reminder_id: int,
        actor_user_id: int | None,
        actor_chat_id: int | None,
        now: datetime,
    ) -> ReminderRecord | None:
        async with self._lock:
            await self._load_locked()
            reminder = self._records.get(reminder_id)
            if reminder is None or not reminder.is_active:
                return None

            if not self._can_access_reminder(
                reminder=reminder,
                actor_user_id=actor_user_id,
                actor_chat_id=actor_chat_id,
            ):
                return None

            completed = replace(
                reminder,
                last_completed_at=now,
                is_active=reminder.recurring,
            )
            self._records[reminder_id] = completed
            await self._save_locked()
            return completed

    async def deactivate_for_chat(self, reminder_id: int, chat_id: int) -> bool:
        async with self._lock:
            await self._load_locked()
            reminder = self._records.get(reminder_id)
            if reminder is None or not reminder.is_active or reminder.chat_id != chat_id:
                return False

            self._records[reminder_id] = replace(reminder, is_active=False)
            await self._save_locked()
            return True

    async def deactivate_by_id(self, reminder_id: int) -> bool:
        async with self._lock:
            await self._load_locked()
            reminder = self._records.get(reminder_id)
            if reminder is None or not reminder.is_active:
                return False

            self._records[reminder_id] = replace(reminder, is_active=False)
            await self._save_locked()
            return True

    async def create(
        self,
        chat_id: int,
        text: str,
        kind: ReminderKind,
        recipient_chat_id: int | None = None,
        timezone: str = DEFAULT_TIMEZONE,
        recurring: bool = False,
        run_at: datetime | None = None,
        daily_time: str | None = None,
        voice: bool = False,
        voice_file_id: str | None = None,
        created_at: datetime | None = None,
    ) -> ReminderRecord:
        zone = _resolve_timezone(timezone)

        safe_run_at: datetime | None = None
        if kind == "once":
            if run_at is None:
                raise ValueError("run_at is required for one-time reminders.")
            safe_run_at = _normalize_datetime(run_at, zone)
        elif daily_time is None:
            raise ValueError("daily_time is required for daily reminders.")

        created = _normalize_datetime(created_at or datetime.now(tz=zone), zone)
        safe_recipient = recipient_chat_id if recipient_chat_id is not None else chat_id

        new_record = ReminderRecord(
            reminder_id=0,
            chat_id=chat_id,
            recipient_chat_id=safe_recipient,
            timezone=zone.key,
            text=text,
            kind=kind,
            recurring=recurring,
            run_at=safe_run_at,
            daily_time=daily_time,
            voice=voice,
            voice_file_id=voice_file_id,
            last_completed_at=None,
            is_active=True,
            created_at=created,
        )

        async with self._lock:
            await self._load_locked()
            persisted = replace(new_record, reminder_id=self._next_id)
            self._next_id += 1
            self._records[persisted.reminder_id] = persisted
            await self._save_locked()
            return persisted

    def _can_access_reminder(
        self,
        reminder: ReminderRecord,
        actor_user_id: int | None,
        actor_chat_id: int | None,
    ) -> bool:
        if actor_user_id is None and actor_chat_id is None:
            return False
        allowed_user = actor_user_id in {reminder.chat_id, reminder.recipient_chat_id}
        allowed_chat = actor_chat_id in {reminder.chat_id, reminder.recipient_chat_id}
        return allowed_user or allowed_chat

    async def _load_locked(self) -> None:
        if self._loaded:
            return

        if not self._db_path.exists():
            self._records = {}
            self._next_id = 1
            self._loaded = True
            return

        raw = await asyncio.to_thread(self._db_path.read_text, encoding="utf-8")
        if not raw.strip():
            self._records = {}
            self._next_id = 1
            self._loaded = True
            return

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            logger.exception(
                "Failed to parse reminders JSON storage '%s'. Reinitializing.",
                self._db_path,
            )
            self._records = {}
            self._next_id = 1
            self._loaded = True
            return

        if not isinstance(payload, list):
            logger.error("Expected list payload in reminders JSON storage '%s'.", self._db_path)
            self._records = {}
            self._next_id = 1
            self._loaded = True
            return

        records: dict[int, ReminderRecord] = {}
        max_id = 0

        for raw_item in payload:
            if not isinstance(raw_item, dict):
                logger.warning("Skipping malformed reminder payload entry: %r", raw_item)
                continue

            item = {str(key): value for key, value in raw_item.items()}
            reminder = self._dict_to_record(item)
            records[reminder.reminder_id] = reminder
            max_id = max(max_id, reminder.reminder_id)

        self._records = records
        self._next_id = max_id + 1
        self._loaded = True

    async def _save_locked(self) -> None:
        payload = [self._record_to_dict(record) for record in self._records.values()]
        raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        await asyncio.to_thread(self._db_path.write_text, raw, encoding="utf-8")

    @staticmethod
    def _sorted_records(reminders: object) -> list[ReminderRecord]:
        items = list(reminders)
        items.sort(key=lambda item: item.reminder_id, reverse=True)
        return items

    @staticmethod
    def _record_to_dict(reminder: ReminderRecord) -> dict[str, object]:
        return {
            "reminder_id": reminder.reminder_id,
            "chat_id": reminder.chat_id,
            "recipient_chat_id": reminder.recipient_chat_id,
            "timezone": reminder.timezone,
            "text": reminder.text,
            "kind": reminder.kind,
            "recurring": reminder.recurring,
            "run_at": reminder.run_at.isoformat() if reminder.run_at is not None else None,
            "daily_time": reminder.daily_time,
            "voice": reminder.voice,
            "voice_file_id": reminder.voice_file_id,
            "last_completed_at": (
                reminder.last_completed_at.isoformat()
                if reminder.last_completed_at is not None
                else None
            ),
            "is_active": reminder.is_active,
            "created_at": reminder.created_at.isoformat(),
        }

    @staticmethod
    def _dict_to_record(item: dict[str, object]) -> ReminderRecord:
        timezone_name = _read_text(item.get("timezone"), default=DEFAULT_TIMEZONE)
        zone = _resolve_timezone(timezone_name)
        raw_kind = _read_text(item.get("kind"), default="once")
        kind: ReminderKind = "daily" if raw_kind == "daily" else "once"

        return ReminderRecord(
            reminder_id=_read_int(item.get("reminder_id")),
            chat_id=_read_int(item.get("chat_id")),
            recipient_chat_id=_read_int(
                item.get("recipient_chat_id"),
                default=_read_int(item.get("chat_id")),
            ),
            timezone=zone.key,
            text=_read_text(item.get("text")),
            kind=kind,
            recurring=_read_bool(item.get("recurring"), default=kind == "daily"),
            run_at=_parse_datetime(_read_optional_text(item.get("run_at")), zone),
            daily_time=_read_optional_text(item.get("daily_time")),
            voice=_read_bool(item.get("voice")),
            voice_file_id=_read_optional_text(item.get("voice_file_id")),
            last_completed_at=_parse_datetime(
                _read_optional_text(item.get("last_completed_at")),
                zone,
            ),
            is_active=_read_bool(item.get("is_active"), default=True),
            created_at=_parse_datetime(
                _read_optional_text(item.get("created_at")),
                zone,
            )
            or datetime.now(tz=zone),
        )


def _resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown timezone '%s', falling back to %s.", name, DEFAULT_TIMEZONE)
        return ZoneInfo(DEFAULT_TIMEZONE)


def _normalize_datetime(value: datetime, zone: ZoneInfo) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _parse_datetime(raw: str | None, zone: ZoneInfo) -> datetime | None:
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return _normalize_datetime(parsed, zone)


def _read_text(value: object, default: str = "") -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return default
    return str(value)


def _read_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = _read_text(value).strip()
    return text or None


def _read_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _read_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default

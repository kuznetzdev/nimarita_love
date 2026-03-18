from __future__ import annotations

from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from zoneinfo import ZoneInfo

from bot.storage import ReminderStorage


class ReminderStorageTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._temp_dir = TemporaryDirectory()
        self._db_path = Path(self._temp_dir.name) / "reminders.json"
        self._storage = ReminderStorage(self._db_path)
        self._timezone = ZoneInfo("Europe/Moscow")
        await self._storage.init_schema()

    async def asyncTearDown(self) -> None:
        self._temp_dir.cleanup()

    async def test_complete_once_deactivates_but_daily_stays_active(self) -> None:
        now = datetime(2026, 3, 18, 12, 0, tzinfo=self._timezone)
        once = await self._storage.create_once(
            chat_id=42,
            text="Разовый сигнал",
            run_at=datetime(2026, 3, 18, 14, 0, tzinfo=self._timezone),
        )
        daily = await self._storage.create_daily(
            chat_id=42,
            text="Ежедневный сигнал",
            daily_time="09:00",
            created_at=now,
        )

        completed_once = await self._storage.complete_by_id(
            reminder_id=once.reminder_id,
            actor_user_id=42,
            actor_chat_id=42,
            now=now,
        )
        completed_daily = await self._storage.complete_by_id(
            reminder_id=daily.reminder_id,
            actor_user_id=42,
            actor_chat_id=42,
            now=now,
        )

        self.assertIsNotNone(completed_once)
        self.assertIsNotNone(completed_daily)
        self.assertFalse(completed_once.is_active)
        self.assertTrue(completed_daily.is_active)
        self.assertEqual(completed_daily.last_completed_at, now)

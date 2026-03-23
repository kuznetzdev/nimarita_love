from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nimarita.config import Settings
from nimarita.domain.errors import ConflictError, ValidationError
from nimarita.domain.enums import (
    ReminderIntervalUnit,
    ReminderOccurrenceStatus,
    ReminderRuleKind,
    ReminderRuleStatus,
)
from nimarita.domain.models import TelegramUserSnapshot
from nimarita.infra import LinkBuilder, SQLiteDatabase
from nimarita.repositories import PairingRepository, ReminderRepository, UserRepository
from nimarita.services import PairingService, ReminderService, UserService


class ReminderServiceTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.db = SQLiteDatabase(self.db_path)
        await self.db.connect()
        self.user_repo = UserRepository(self.db, default_timezone="Europe/Moscow")
        self.pairing_repo = PairingRepository(self.db)
        self.reminder_repo = ReminderRepository(self.db)
        self.settings = Settings(
            bot_token="123:TEST",
            bot_username="testbot",
            webapp_public_url="https://example.com/app",
            webapp_enabled=True,
            webapp_host="127.0.0.1",
            webapp_port=8080,
            database_path=self.db_path,
            log_level="INFO",
            default_timezone="Europe/Moscow",
            init_data_ttl_seconds=3600,
            session_ttl_seconds=3600,
            session_secret="secret",
            pair_invite_ttl_minutes=60,
            mini_app_short_name=None,
            mini_app_title="Test",
            reminder_worker_poll_seconds=5,
            reminder_batch_size=20,
            reminder_max_retries=4,
            reminder_retry_base_seconds=30,
            cleanup_worker_poll_seconds=8,
            cleanup_batch_size=25,
            action_message_ttl_seconds=12,
            notice_message_ttl_seconds=20,
            welcome_message_ttl_seconds=25,
            care_per_minute_limit=6,
            care_per_hour_limit=40,
            care_duplicate_window_minutes=20,
            care_history_limit=60,
            care_sender_notice_ttl_seconds=24,
        )
        self.users = UserService(self.user_repo)
        self.pairing = PairingService(
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=self.settings,
            links=LinkBuilder(self.settings),
            reminders=self.reminder_repo,
        )
        self.reminders = ReminderService(
            reminders=self.reminder_repo,
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=self.settings,
        )

        await self.users.ensure_bot_user(
            TelegramUserSnapshot(
                telegram_user_id=101,
                chat_id=101,
                username="alice",
                first_name="Alice",
                last_name=None,
                language_code="ru",
            )
        )
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(
                telegram_user_id=202,
                chat_id=202,
                username="bob",
                first_name="Bob",
                last_name=None,
                language_code="ru",
            )
        )
        invite = await self.pairing.create_invite(101)
        await self.pairing.accept_invite_by_token(202, invite.raw_token)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()

    async def test_create_and_cancel_reminder(self) -> None:
        envelope = await self.reminders.create_one_time_reminder(
            telegram_user_id=101,
            text="test reminder",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
        )
        self.assertEqual(envelope.occurrence.status, ReminderOccurrenceStatus.SCHEDULED)

        cancelled = await self.reminders.cancel_reminder(
            telegram_user_id=101,
            rule_id=envelope.rule.id,
        )
        self.assertEqual(cancelled.occurrence.status, ReminderOccurrenceStatus.CANCELLED)

    async def test_duplicate_submit_reuses_equivalent_active_reminder(self) -> None:
        first = await self.reminders.create_one_time_reminder(
            telegram_user_id=101,
            text="drink water",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
        )
        second = await self.reminders.create_one_time_reminder(
            telegram_user_id=101,
            text="drink water",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
        )

        self.assertEqual(second.rule.id, first.rule.id)
        self.assertEqual(second.occurrence.id, first.occurrence.id)
        reminders = await self.reminders.list_pair_reminders(telegram_user_id=101, limit=10)
        same_rule_items = [item for item in reminders if item.rule.id == first.rule.id]
        self.assertEqual(len(same_rule_items), 1)

    async def test_acknowledge_and_snooze(self) -> None:
        envelope = await self.reminders.create_one_time_reminder(
            telegram_user_id=101,
            text="check recipient actions",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
        )
        claimed = await self.reminders.claim_due_occurrences(limit=10)
        self.assertEqual(len(claimed), 0)

        marked = await self.reminder_repo.mark_delivered(
            occurrence_id=envelope.occurrence.id,
            telegram_message_id=555,
            now=envelope.occurrence.created_at,
        )
        self.assertEqual(marked.status, ReminderOccurrenceStatus.DELIVERED)

        done = await self.reminders.acknowledge(telegram_user_id=202, occurrence_id=envelope.occurrence.id)
        self.assertEqual(done.occurrence.status, ReminderOccurrenceStatus.ACKNOWLEDGED)

        second = await self.reminders.create_one_time_reminder(
            telegram_user_id=101,
            text="snooze me",
            scheduled_for_local="2030-01-01T11:00",
            timezone="Europe/Moscow",
        )
        await self.reminder_repo.mark_delivered(
            occurrence_id=second.occurrence.id,
            telegram_message_id=777,
            now=second.occurrence.created_at,
        )
        current, follow_up = await self.reminders.snooze(
            telegram_user_id=202,
            occurrence_id=second.occurrence.id,
            minutes=10,
        )
        self.assertEqual(current.occurrence.status, ReminderOccurrenceStatus.ACKNOWLEDGED)
        self.assertEqual(follow_up.occurrence.status, ReminderOccurrenceStatus.SCHEDULED)
        self.assertNotEqual(current.occurrence.id, follow_up.occurrence.id)

    async def test_daily_reminder_schedules_next_occurrence_after_delivery(self) -> None:
        envelope = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="daily cadence",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.DAILY,
        )

        delivered = await self.reminders.mark_delivered(
            occurrence_id=envelope.occurrence.id,
            telegram_message_id=4242,
        )
        self.assertEqual(delivered.occurrence.status, ReminderOccurrenceStatus.DELIVERED)

        scheduled = await self.reminders.list_pair_reminders(telegram_user_id=101, limit=10)
        follow_up = next(
            item
            for item in scheduled
            if item.rule.id == envelope.rule.id and item.occurrence.id != envelope.occurrence.id
        )
        self.assertEqual(follow_up.rule.kind, ReminderRuleKind.DAILY)
        self.assertEqual(follow_up.occurrence.status, ReminderOccurrenceStatus.SCHEDULED)
        self.assertEqual(
            follow_up.occurrence.scheduled_at_utc,
            envelope.occurrence.scheduled_at_utc + timedelta(days=1),
        )

    async def test_weekdays_reminder_skips_weekend_after_delivery(self) -> None:
        envelope = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="weekday cadence",
            scheduled_for_local="2030-01-04T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.WEEKDAYS,
        )

        delivered = await self.reminders.mark_delivered(
            occurrence_id=envelope.occurrence.id,
            telegram_message_id=5151,
        )
        self.assertEqual(delivered.occurrence.status, ReminderOccurrenceStatus.DELIVERED)

        scheduled = await self.reminders.list_pair_reminders(telegram_user_id=101, limit=10)
        follow_up = next(
            item
            for item in scheduled
            if item.rule.id == envelope.rule.id and item.occurrence.id != envelope.occurrence.id
        )
        expected = datetime(2030, 1, 7, 10, 0, tzinfo=ZoneInfo("Europe/Moscow")).astimezone(UTC)
        self.assertEqual(follow_up.occurrence.status, ReminderOccurrenceStatus.SCHEDULED)
        self.assertEqual(follow_up.occurrence.scheduled_at_utc, expected)

    async def test_weekly_reminder_schedules_next_occurrence_after_delivery(self) -> None:
        envelope = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="weekly cadence",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.WEEKLY,
        )

        delivered = await self.reminders.mark_delivered(
            occurrence_id=envelope.occurrence.id,
            telegram_message_id=6161,
        )
        self.assertEqual(delivered.occurrence.status, ReminderOccurrenceStatus.DELIVERED)

        scheduled = await self.reminders.list_pair_reminders(telegram_user_id=101, limit=10)
        follow_up = next(
            item
            for item in scheduled
            if item.rule.id == envelope.rule.id and item.occurrence.id != envelope.occurrence.id
        )
        self.assertEqual(follow_up.occurrence.status, ReminderOccurrenceStatus.SCHEDULED)
        self.assertEqual(
            follow_up.occurrence.scheduled_at_utc,
            envelope.occurrence.scheduled_at_utc + timedelta(weeks=1),
        )

    async def test_interval_reminder_schedules_next_occurrence_after_delivery(self) -> None:
        envelope = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="every two weeks",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.INTERVAL,
            recurrence_every=2,
            recurrence_unit=ReminderIntervalUnit.WEEK,
        )

        delivered = await self.reminders.mark_delivered(
            occurrence_id=envelope.occurrence.id,
            telegram_message_id=7777,
        )
        self.assertEqual(delivered.occurrence.status, ReminderOccurrenceStatus.DELIVERED)

        scheduled = await self.reminders.list_pair_reminders(telegram_user_id=101, limit=10)
        follow_up = next(
            item
            for item in scheduled
            if item.rule.id == envelope.rule.id and item.occurrence.id != envelope.occurrence.id
        )
        self.assertEqual(follow_up.rule.kind, ReminderRuleKind.INTERVAL)
        self.assertEqual(follow_up.rule.recurrence_every, 2)
        self.assertEqual(follow_up.rule.recurrence_unit, ReminderIntervalUnit.WEEK)
        self.assertEqual(
            follow_up.occurrence.scheduled_at_utc,
            envelope.occurrence.scheduled_at_utc + timedelta(weeks=2),
        )

    async def test_interval_reminder_schedules_next_occurrence_for_all_supported_units(self) -> None:
        cases = (
            (
                "hours",
                "2030-01-01T10:00",
                6,
                ReminderIntervalUnit.HOUR,
                datetime(2030, 1, 1, 16, 0, tzinfo=ZoneInfo("Europe/Moscow")).astimezone(UTC),
            ),
            (
                "days",
                "2030-01-01T10:00",
                3,
                ReminderIntervalUnit.DAY,
                datetime(2030, 1, 4, 10, 0, tzinfo=ZoneInfo("Europe/Moscow")).astimezone(UTC),
            ),
            (
                "months",
                "2030-01-31T10:00",
                1,
                ReminderIntervalUnit.MONTH,
                datetime(2030, 2, 28, 10, 0, tzinfo=ZoneInfo("Europe/Moscow")).astimezone(UTC),
            ),
        )
        for label, scheduled_for_local, every, unit, expected in cases:
            with self.subTest(interval=label):
                envelope = await self.reminders.create_reminder(
                    telegram_user_id=101,
                    text=f"interval {label}",
                    scheduled_for_local=scheduled_for_local,
                    timezone="Europe/Moscow",
                    kind=ReminderRuleKind.INTERVAL,
                    recurrence_every=every,
                    recurrence_unit=unit,
                )

                delivered = await self.reminders.mark_delivered(
                    occurrence_id=envelope.occurrence.id,
                    telegram_message_id=8000 + every,
                )
                self.assertEqual(delivered.occurrence.status, ReminderOccurrenceStatus.DELIVERED)

                scheduled = await self.reminders.list_pair_reminders(telegram_user_id=101, limit=20)
                follow_up = next(
                    item
                    for item in scheduled
                    if item.rule.id == envelope.rule.id and item.occurrence.id != envelope.occurrence.id
                )
                self.assertEqual(follow_up.rule.recurrence_every, every)
                self.assertEqual(follow_up.rule.recurrence_unit, unit)
                self.assertEqual(follow_up.occurrence.scheduled_at_utc, expected)

    async def test_interval_reminder_requires_recurrence_unit(self) -> None:
        with self.assertRaises(ValidationError):
            await self.reminders.create_reminder(
                telegram_user_id=101,
                text="missing unit",
                scheduled_for_local="2030-01-01T10:00",
                timezone="Europe/Moscow",
                kind=ReminderRuleKind.INTERVAL,
                recurrence_every=2,
                recurrence_unit=None,
            )

    async def test_interval_reminder_rejects_non_positive_recurrence_every(self) -> None:
        with self.assertRaises(ValidationError):
            await self.reminders.create_reminder(
                telegram_user_id=101,
                text="bad cadence",
                scheduled_for_local="2030-01-01T10:00",
                timezone="Europe/Moscow",
                kind=ReminderRuleKind.INTERVAL,
                recurrence_every=0,
                recurrence_unit=ReminderIntervalUnit.DAY,
            )

    async def test_edit_reminder_updates_text_and_interval(self) -> None:
        created = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="old text",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.DAILY,
        )

        updated = await self.reminders.update_reminder(
            telegram_user_id=101,
            rule_id=created.rule.id,
            text="new text",
            scheduled_for_local="2030-01-05T09:30",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.INTERVAL,
            recurrence_every=3,
            recurrence_unit=ReminderIntervalUnit.DAY,
        )

        self.assertEqual(updated.rule.kind, ReminderRuleKind.INTERVAL)
        self.assertEqual(updated.rule.recurrence_every, 3)
        self.assertEqual(updated.rule.recurrence_unit, ReminderIntervalUnit.DAY)
        self.assertEqual(updated.occurrence.text, "new text")
        self.assertEqual(updated.occurrence.status, ReminderOccurrenceStatus.SCHEDULED)

    async def test_update_reminder_replaces_all_open_occurrences_for_rule(self) -> None:
        created = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="replace future slots",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.DAILY,
        )
        await self.reminders.mark_delivered(
            occurrence_id=created.occurrence.id,
            telegram_message_id=9001,
        )
        await self.reminders.snooze(
            telegram_user_id=202,
            occurrence_id=created.occurrence.id,
            minutes=10,
        )

        updated = await self.reminders.update_reminder(
            telegram_user_id=101,
            rule_id=created.rule.id,
            text="fresh cadence",
            scheduled_for_local="2030-01-05T09:30",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.INTERVAL,
            recurrence_every=3,
            recurrence_unit=ReminderIntervalUnit.DAY,
        )

        reminders = await self.reminders.list_pair_reminders(telegram_user_id=101, limit=20)
        rule_entries = [item for item in reminders if item.rule.id == created.rule.id]
        scheduled_entries = [item for item in rule_entries if item.occurrence.status is ReminderOccurrenceStatus.SCHEDULED]
        cancelled_entries = [item for item in rule_entries if item.occurrence.status is ReminderOccurrenceStatus.CANCELLED]

        self.assertEqual(updated.rule.kind, ReminderRuleKind.INTERVAL)
        self.assertEqual(len(scheduled_entries), 1)
        self.assertEqual(scheduled_entries[0].occurrence.text, "fresh cadence")
        self.assertGreaterEqual(len(cancelled_entries), 1)

    async def test_cancel_reminder_cancels_all_open_occurrences_for_rule(self) -> None:
        created = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="stop future slots",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.DAILY,
        )
        await self.reminders.mark_delivered(
            occurrence_id=created.occurrence.id,
            telegram_message_id=9002,
        )
        await self.reminders.snooze(
            telegram_user_id=202,
            occurrence_id=created.occurrence.id,
            minutes=15,
        )

        cancelled = await self.reminders.cancel_reminder(
            telegram_user_id=101,
            rule_id=created.rule.id,
        )

        reminders = await self.reminders.list_pair_reminders(telegram_user_id=101, limit=20)
        rule_entries = [item for item in reminders if item.rule.id == created.rule.id]
        scheduled_entries = [item for item in rule_entries if item.occurrence.status is ReminderOccurrenceStatus.SCHEDULED]
        cancelled_entries = [item for item in rule_entries if item.occurrence.status is ReminderOccurrenceStatus.CANCELLED]

        self.assertEqual(cancelled.rule.status, ReminderRuleStatus.CANCELLED)
        self.assertEqual(cancelled.occurrence.status, ReminderOccurrenceStatus.CANCELLED)
        self.assertEqual(scheduled_entries, [])
        self.assertGreaterEqual(len(cancelled_entries), 2)

    async def test_restore_cancelled_reminder_reactivates_rule_with_fresh_schedule(self) -> None:
        created = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="paused cadence",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.DAILY,
        )
        await self.reminders.cancel_reminder(
            telegram_user_id=101,
            rule_id=created.rule.id,
        )

        restored = await self.reminders.restore_reminder(
            telegram_user_id=101,
            rule_id=created.rule.id,
            text="resumed cadence",
            scheduled_for_local="2030-01-10T08:45",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.INTERVAL,
            recurrence_every=3,
            recurrence_unit=ReminderIntervalUnit.DAY,
        )

        reminders = await self.reminders.list_pair_reminders(telegram_user_id=101, limit=20)
        rule_entries = [item for item in reminders if item.rule.id == created.rule.id]
        scheduled_entries = [item for item in rule_entries if item.occurrence.status is ReminderOccurrenceStatus.SCHEDULED]
        cancelled_entries = [item for item in rule_entries if item.occurrence.status is ReminderOccurrenceStatus.CANCELLED]

        self.assertEqual(restored.rule.status, ReminderRuleStatus.ACTIVE)
        self.assertEqual(restored.rule.kind, ReminderRuleKind.INTERVAL)
        self.assertEqual(restored.rule.recurrence_every, 3)
        self.assertEqual(restored.rule.recurrence_unit, ReminderIntervalUnit.DAY)
        self.assertEqual(restored.occurrence.text, "resumed cadence")
        self.assertEqual(restored.occurrence.status, ReminderOccurrenceStatus.SCHEDULED)
        self.assertEqual(len(scheduled_entries), 1)
        self.assertGreaterEqual(len(cancelled_entries), 1)

    async def test_non_creator_cannot_edit_or_cancel_reminder(self) -> None:
        created = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="creator-owned reminder",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.ONE_TIME,
        )

        with self.assertRaises(ConflictError):
            await self.reminders.update_reminder(
                telegram_user_id=202,
                rule_id=created.rule.id,
                text="tamper",
                scheduled_for_local="2030-01-02T10:00",
                timezone="Europe/Moscow",
                kind=ReminderRuleKind.DAILY,
            )

        with self.assertRaises(ConflictError):
            await self.reminders.cancel_reminder(
                telegram_user_id=202,
                rule_id=created.rule.id,
            )

        await self.reminders.cancel_reminder(
            telegram_user_id=101,
            rule_id=created.rule.id,
        )

        with self.assertRaises(ConflictError):
            await self.reminders.restore_reminder(
                telegram_user_id=202,
                rule_id=created.rule.id,
                text="tamper restore",
                scheduled_for_local="2030-01-03T10:00",
                timezone="Europe/Moscow",
                kind=ReminderRuleKind.DAILY,
            )

    async def test_claim_due_occurrences_ignores_scheduled_rows_under_cancelled_rule(self) -> None:
        created = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="legacy stale row",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.ONE_TIME,
        )
        await self.reminders.cancel_reminder(
            telegram_user_id=101,
            rule_id=created.rule.id,
        )
        stale_due_at = datetime.now(tz=UTC) - timedelta(minutes=5)
        await self.db.execute(
            """
            UPDATE reminder_occurrences
            SET status = ?, cancelled_at = NULL, next_attempt_at_utc = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                ReminderOccurrenceStatus.SCHEDULED.value,
                stale_due_at.isoformat(),
                stale_due_at.isoformat(),
                created.occurrence.id,
            ),
        )

        claimed = await self.reminders.claim_due_occurrences(limit=10)
        self.assertEqual(claimed, [])

    async def test_recurring_reminder_persists_recurrence_fields_across_reconnect(self) -> None:
        created = await self.reminders.create_reminder(
            telegram_user_id=101,
            text="persisted cadence",
            scheduled_for_local="2030-01-31T10:00",
            timezone="Europe/Moscow",
            kind=ReminderRuleKind.INTERVAL,
            recurrence_every=1,
            recurrence_unit=ReminderIntervalUnit.MONTH,
        )

        await self.db.close()

        db = SQLiteDatabase(self.db_path)
        await db.connect()
        user_repo = UserRepository(db, default_timezone="Europe/Moscow")
        pairing_repo = PairingRepository(db)
        reminder_repo = ReminderRepository(db)
        reminders = ReminderService(
            reminders=reminder_repo,
            pairing=pairing_repo,
            users=user_repo,
            settings=self.settings,
        )
        try:
            persisted = await reminders.list_pair_reminders(telegram_user_id=101, limit=10)
            item = next(entry for entry in persisted if entry.rule.id == created.rule.id)
            self.assertEqual(item.rule.kind, ReminderRuleKind.INTERVAL)
            self.assertEqual(item.rule.recurrence_every, 1)
            self.assertEqual(item.rule.recurrence_unit, ReminderIntervalUnit.MONTH)
            self.assertEqual(item.rule.origin_scheduled_at_utc, created.rule.origin_scheduled_at_utc)
        finally:
            await db.close()
            self.db = SQLiteDatabase(self.db_path)
            await self.db.connect()


class ReminderPersistenceCompatTestCase(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_reminder_schema_migrates_recurrence_columns_additively(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.db"
            now = datetime(2030, 1, 1, 7, 0, tzinfo=UTC).isoformat()
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(
                    """
                    CREATE TABLE reminder_rules (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        pair_id INTEGER NOT NULL,
                        creator_user_id INTEGER NOT NULL,
                        recipient_user_id INTEGER NOT NULL,
                        kind TEXT NOT NULL,
                        text TEXT NOT NULL,
                        creator_timezone TEXT NOT NULL,
                        origin_scheduled_at_utc TEXT NOT NULL,
                        status TEXT NOT NULL,
                        cancelled_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                connection.execute(
                    """
                    INSERT INTO reminder_rules (
                        pair_id,
                        creator_user_id,
                        recipient_user_id,
                        kind,
                        text,
                        creator_timezone,
                        origin_scheduled_at_utc,
                        status,
                        cancelled_at,
                        created_at,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (1, 101, 202, "daily", "legacy row", "Europe/Moscow", now, "active", now, now),
                )
                connection.commit()
            finally:
                connection.close()

            db = SQLiteDatabase(db_path)
            await db.connect()
            try:
                repo = ReminderRepository(db)
                rule = await repo.get_rule(1)
                self.assertIsNotNone(rule)
                assert rule is not None
                self.assertEqual(rule.recurrence_every, 1)
                self.assertIsNone(rule.recurrence_unit)
                columns = await db.fetchall("PRAGMA table_info(reminder_rules)")
                column_names = {row["name"] for row in columns}
                self.assertIn("recurrence_every", column_names)
                self.assertIn("recurrence_unit", column_names)
            finally:
                await db.close()


if __name__ == "__main__":
    unittest.main()

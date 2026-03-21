from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nimarita.config import Settings
from datetime import timedelta

from nimarita.domain.enums import ReminderIntervalUnit, ReminderOccurrenceStatus, ReminderRuleKind
from nimarita.domain.models import TelegramUserSnapshot
from nimarita.infra import LinkBuilder, SQLiteDatabase
from nimarita.repositories import PairingRepository, ReminderRepository, UserRepository
from nimarita.services import PairingService, ReminderService, UserService


class ReminderServiceTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        self.db = SQLiteDatabase(db_path)
        await self.db.connect()
        self.user_repo = UserRepository(self.db, default_timezone="Europe/Moscow")
        self.pairing_repo = PairingRepository(self.db)
        self.reminder_repo = ReminderRepository(self.db)
        settings = Settings(
            bot_token="123:TEST",
            bot_username="testbot",
            webapp_public_url="https://example.com/app",
            webapp_enabled=True,
            webapp_host="127.0.0.1",
            webapp_port=8080,
            database_path=db_path,
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
            settings=settings,
            links=LinkBuilder(settings),
            reminders=self.reminder_repo,
        )
        self.reminders = ReminderService(
            reminders=self.reminder_repo,
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=settings,
        )

        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=101, chat_id=101, username="alice", first_name="Alice", last_name=None, language_code="ru")
        )
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=202, chat_id=202, username="bob", first_name="Bob", last_name=None, language_code="ru")
        )
        invite = await self.pairing.create_invite(101)
        await self.pairing.accept_invite_by_token(202, invite.raw_token)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()

    async def test_create_and_cancel_reminder(self) -> None:
        envelope = await self.reminders.create_one_time_reminder(
            telegram_user_id=101,
            text="Протестировать напоминание",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
        )
        self.assertEqual(envelope.occurrence.status, ReminderOccurrenceStatus.SCHEDULED)

        cancelled = await self.reminders.cancel_reminder(
            telegram_user_id=101,
            rule_id=envelope.rule.id,
        )
        self.assertEqual(cancelled.occurrence.status, ReminderOccurrenceStatus.CANCELLED)

    async def test_acknowledge_and_snooze(self) -> None:
        envelope = await self.reminders.create_one_time_reminder(
            telegram_user_id=101,
            text="Проверить кнопки",
            scheduled_for_local="2030-01-01T10:00",
            timezone="Europe/Moscow",
        )
        claimed = await self.reminders.claim_due_occurrences(limit=10)
        self.assertEqual(len(claimed), 0)

        # Делаем occurrence доставленным напрямую через репозиторий, чтобы протестировать recipient actions.
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
            text="Отложить меня",
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
            text="Напомни мне написать",
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
        self.assertGreaterEqual(len(scheduled), 2)
        follow_up = next(item for item in scheduled if item.occurrence.id != envelope.occurrence.id)
        self.assertEqual(follow_up.rule.id, envelope.rule.id)
        self.assertEqual(follow_up.rule.kind, ReminderRuleKind.DAILY)
        self.assertEqual(follow_up.occurrence.status, ReminderOccurrenceStatus.SCHEDULED)
        self.assertEqual(
            follow_up.occurrence.scheduled_at_utc,
            envelope.occurrence.scheduled_at_utc + timedelta(days=1),
        )

    async def test_interval_reminder_schedules_next_occurrence_after_delivery(self) -> None:
        envelope = await self.reminders.create_reminder(
            telegram_user_id=101,
            text='Напомни мне раз в две недели',
            scheduled_for_local='2030-01-01T10:00',
            timezone='Europe/Moscow',
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
        follow_up = next(item for item in scheduled if item.occurrence.id != envelope.occurrence.id)
        self.assertEqual(follow_up.rule.kind, ReminderRuleKind.INTERVAL)
        self.assertEqual(follow_up.rule.recurrence_every, 2)
        self.assertEqual(follow_up.rule.recurrence_unit, ReminderIntervalUnit.WEEK)
        self.assertEqual(
            follow_up.occurrence.scheduled_at_utc,
            envelope.occurrence.scheduled_at_utc + timedelta(weeks=2),
        )

    async def test_edit_reminder_updates_text_and_interval(self) -> None:
        created = await self.reminders.create_reminder(
            telegram_user_id=101,
            text='Старый текст',
            scheduled_for_local='2030-01-01T10:00',
            timezone='Europe/Moscow',
            kind=ReminderRuleKind.DAILY,
        )

        updated = await self.reminders.update_reminder(
            telegram_user_id=101,
            rule_id=created.rule.id,
            text='Новый текст',
            scheduled_for_local='2030-01-05T09:30',
            timezone='Europe/Moscow',
            kind=ReminderRuleKind.INTERVAL,
            recurrence_every=3,
            recurrence_unit=ReminderIntervalUnit.DAY,
        )

        self.assertEqual(updated.rule.kind, ReminderRuleKind.INTERVAL)
        self.assertEqual(updated.rule.recurrence_every, 3)
        self.assertEqual(updated.rule.recurrence_unit, ReminderIntervalUnit.DAY)
        self.assertEqual(updated.occurrence.text, 'Новый текст')
        self.assertEqual(updated.occurrence.status, ReminderOccurrenceStatus.SCHEDULED)


if __name__ == "__main__":
    unittest.main()

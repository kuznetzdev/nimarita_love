from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nimarita.config import Settings
from nimarita.domain.models import TelegramUserSnapshot
from nimarita.infra import LinkBuilder, SQLiteDatabase
from nimarita.repositories import CareRepository, PairingRepository, ReminderRepository, UserRepository
from nimarita.services import CareService, PairingService, ReminderService, UserService


class ReliabilityServiceTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / 'test.db'
        self.db = SQLiteDatabase(db_path)
        await self.db.connect()
        self.user_repo = UserRepository(self.db, default_timezone='Europe/Moscow')
        self.pairing_repo = PairingRepository(self.db)
        self.reminder_repo = ReminderRepository(self.db)
        self.care_repo = CareRepository(self.db)
        settings = Settings(
            bot_token='123:TEST',
            bot_username='testbot',
            webapp_public_url='https://example.com/app',
            webapp_enabled=True,
            webapp_host='127.0.0.1',
            webapp_port=8080,
            database_path=db_path,
            log_level='INFO',
            default_timezone='Europe/Moscow',
            init_data_ttl_seconds=3600,
            session_ttl_seconds=3600,
            session_secret='secret',
            pair_invite_ttl_minutes=60,
            mini_app_short_name=None,
            mini_app_title='Test',
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
            processing_stale_seconds=0,
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
        self.care = CareService(
            care=self.care_repo,
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=settings,
        )
        await self.care.ensure_seeded()

        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=101, chat_id=101, username='alice', first_name='Alice', last_name=None, language_code='ru')
        )
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=202, chat_id=202, username='bob', first_name='Bob', last_name=None, language_code='ru')
        )
        invite = await self.pairing.create_invite(101)
        await self.pairing.accept_invite_by_token(202, invite.raw_token)

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()

    async def test_queue_care_dispatch_claim_and_retry(self) -> None:
        templates = await self.care.list_templates(telegram_user_id=101)
        chosen = templates[0]

        queued = await self.care.queue_template(telegram_user_id=101, template_code=chosen.template_code)
        self.assertEqual(queued.dispatch.status.value, 'pending')

        claimed = await self.care.claim_due_dispatches(limit=10)
        self.assertTrue(claimed)
        claimed_item = next(item for item in claimed if item.dispatch.id == queued.dispatch.id)
        self.assertEqual(claimed_item.dispatch.status.value, 'processing')
        self.assertEqual(claimed_item.dispatch.delivery_attempts_count, 1)

        failure = await self.care.mark_delivery_failure(dispatch_id=claimed_item.dispatch.id, error_text='network')
        self.assertFalse(failure.final_failure)
        self.assertEqual(failure.dispatch.status.value, 'pending')
        self.assertIsNotNone(failure.dispatch.next_attempt_at_utc)

    async def test_recover_stale_processing_reminder(self) -> None:
        alice = await self.user_repo.get_by_telegram_user_id(101)
        bob = await self.user_repo.get_by_telegram_user_id(202)
        assert alice is not None and bob is not None
        pair = await self.pairing_repo.get_active_pair_for_user(alice.id)
        assert pair is not None

        past = datetime.now(tz=UTC) - timedelta(seconds=5)
        _rule, occurrence = await self.reminder_repo.create_one_time_reminder(
            pair_id=pair.id,
            creator_user_id=alice.id,
            recipient_user_id=bob.id,
            text='ping',
            creator_timezone='Europe/Moscow',
            scheduled_at_utc=past,
            now=past,
        )
        claimed = await self.reminders.claim_due_occurrences(limit=10)
        self.assertTrue(any(item.occurrence.id == occurrence.id for item in claimed))

        recovered = await self.reminders.recover_stale_processing()
        self.assertEqual(recovered, 1)

        refreshed = await self.reminder_repo.get_occurrence(occurrence.id)
        assert refreshed is not None
        self.assertEqual(refreshed.status.value, 'scheduled')


if __name__ == '__main__':
    unittest.main()

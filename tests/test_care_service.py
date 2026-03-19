from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nimarita.config import Settings
from nimarita.domain.errors import ConflictError
from nimarita.domain.models import TelegramUserSnapshot
from nimarita.infra import LinkBuilder, SQLiteDatabase
from nimarita.repositories import CareRepository, PairingRepository, ReminderRepository, UserRepository
from nimarita.services import CareService, PairingService, UserService


class CareServiceTestCase(unittest.IsolatedAsyncioTestCase):
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
        )
        self.users = UserService(self.user_repo)
        self.pairing = PairingService(
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=settings,
            links=LinkBuilder(settings),
            reminders=self.reminder_repo,
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

    async def test_catalog_is_seeded_with_large_amount_of_templates(self) -> None:
        templates = await self.care.list_templates(telegram_user_id=101)
        self.assertGreaterEqual(len(templates), 120)

    async def test_send_and_reply_to_care_message(self) -> None:
        templates = await self.care.list_templates(telegram_user_id=101)
        chosen = templates[0]

        async def fake_deliver(envelope):
            self.assertEqual(envelope.dispatch.template_code, chosen.template_code)
            return 999

        sent = await self.care.send_template_now(
            telegram_user_id=101,
            template_code=chosen.template_code,
            deliver=fake_deliver,
        )
        self.assertEqual(sent.dispatch.telegram_message_id, 999)
        self.assertEqual(sent.dispatch.status.value, 'sent')

        result = await self.care.register_quick_reply(
            telegram_user_id=202,
            dispatch_id=sent.dispatch.id,
            reply_code='thanks_love',
        )
        self.assertEqual(result.envelope.dispatch.status.value, 'responded')
        self.assertEqual(result.reply.code, 'thanks_love')

    async def test_duplicate_template_in_window_is_blocked(self) -> None:
        templates = await self.care.list_templates(telegram_user_id=101)
        chosen = templates[0]

        async def fake_deliver(_envelope):
            return 1001

        await self.care.send_template_now(
            telegram_user_id=101,
            template_code=chosen.template_code,
            deliver=fake_deliver,
        )
        with self.assertRaises(ConflictError):
            await self.care.send_template_now(
                telegram_user_id=101,
                template_code=chosen.template_code,
                deliver=fake_deliver,
            )


if __name__ == '__main__':
    unittest.main()

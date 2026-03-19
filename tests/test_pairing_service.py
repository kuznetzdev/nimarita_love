from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nimarita.config import Settings
from nimarita.domain.errors import ConflictError
from nimarita.domain.models import TelegramUserSnapshot
from nimarita.infra import LinkBuilder, SQLiteDatabase
from nimarita.repositories import PairingRepository, ReminderRepository, UserRepository
from nimarita.services import PairingService, UserService


class PairingServiceTestCase(unittest.IsolatedAsyncioTestCase):
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

        for telegram_user_id, username, first_name in [
            (101, "alice", "Alice"),
            (202, "bob", "Bob"),
            (303, "carol", "Carol"),
        ]:
            await self.users.ensure_bot_user(
                TelegramUserSnapshot(
                    telegram_user_id=telegram_user_id,
                    chat_id=telegram_user_id,
                    username=username,
                    first_name=first_name,
                    last_name=None,
                    language_code="ru",
                )
            )

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()

    async def test_create_and_accept_invite_creates_active_pair(self) -> None:
        result = await self.pairing.create_invite(101)
        pair, inviter, invitee = await self.pairing.accept_invite_by_token(202, result.raw_token)

        self.assertEqual(inviter.telegram_user_id, 101)
        self.assertEqual(invitee.telegram_user_id, 202)
        self.assertTrue(pair.includes(inviter.id))
        self.assertTrue(pair.includes(invitee.id))

    async def test_second_active_pair_is_blocked(self) -> None:
        result = await self.pairing.create_invite(101)
        await self.pairing.accept_invite_by_token(202, result.raw_token)

        with self.assertRaises(ConflictError):
            await self.pairing.create_invite(101)

        with self.assertRaises(ConflictError):
            second = await self.pairing.create_invite(303)
            await self.pairing.accept_invite_by_token(202, second.raw_token)


if __name__ == "__main__":
    unittest.main()

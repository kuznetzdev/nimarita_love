from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from nimarita.config import Settings
from nimarita.domain.errors import ConflictError, NotFoundError
from nimarita.domain.enums import CareDispatchStatus
from nimarita.domain.models import TelegramUserSnapshot
from nimarita.infra import LinkBuilder, SQLiteDatabase
from nimarita.repositories import CareRepository, PairingRepository, ReminderRepository, UserRepository
from nimarita.services import CareService, PairingService, UserService
from nimarita.telegram.keyboards import dashboard_keyboard


class PairingHardeningTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / 'test.db'
        self.db = SQLiteDatabase(db_path)
        await self.db.connect()
        self.user_repo = UserRepository(self.db, default_timezone='Europe/Moscow')
        self.pairing_repo = PairingRepository(self.db)
        self.reminder_repo = ReminderRepository(self.db)
        self.care_repo = CareRepository(self.db)
        self.settings = Settings(
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
            settings=self.settings,
            links=LinkBuilder(self.settings),
            reminders=self.reminder_repo,
            care=self.care_repo,
        )
        self.care = CareService(
            care=self.care_repo,
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=self.settings,
        )
        await self.care.ensure_seeded()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()

    async def test_invite_preview_binds_invitee_and_dashboard_keeps_incoming_state(self) -> None:
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=101, chat_id=101, username='alice', first_name='Alice', last_name=None, language_code='ru')
        )
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=202, chat_id=202, username='bob', first_name='Bob', last_name=None, language_code='ru')
        )

        invite = await self.pairing.create_invite(101)
        preview = await self.pairing.preview_invite(202, invite.raw_token)
        dashboard = await self.pairing.get_dashboard(202)

        self.assertEqual(preview.invite.id, invite.invite.id)
        self.assertEqual(preview.inviter.telegram_user_id, 101)
        self.assertEqual(dashboard.mode, 'incoming_invite')
        self.assertIsNotNone(dashboard.incoming_invite)
        self.assertEqual(dashboard.incoming_invite.id, invite.invite.id)
        self.assertIsNotNone(dashboard.incoming_inviter)
        self.assertEqual(dashboard.incoming_inviter.telegram_user_id, 101)

        stored = await self.pairing_repo.get_pending_invite_by_id(invite.invite.id)
        self.assertIsNotNone(stored)
        bob = await self.user_repo.get_by_telegram_user_id(202)
        assert bob is not None and stored is not None
        self.assertEqual(stored.invitee_user_id, bob.id)

    async def test_web_only_preview_does_not_bind_invitee_until_bot_start(self) -> None:
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=101, chat_id=101, username='alice', first_name='Alice', last_name=None, language_code='ru')
        )
        await self.users.touch_web_user(
            TelegramUserSnapshot(telegram_user_id=202, chat_id=None, username='bob', first_name='Bob', last_name=None, language_code='ru')
        )

        invite = await self.pairing.create_invite(101)
        preview = await self.pairing.preview_invite(202, invite.raw_token)
        dashboard = await self.pairing.get_dashboard(202)

        self.assertEqual(preview.invite.id, invite.invite.id)
        self.assertEqual(dashboard.mode, 'no_pair')
        self.assertIsNone(dashboard.incoming_invite)

        stored = await self.pairing_repo.get_pending_invite_by_id(invite.invite.id)
        self.assertIsNotNone(stored)
        assert stored is not None
        self.assertIsNone(stored.invitee_user_id)

        with self.assertRaises(ConflictError):
            await self.pairing.accept_invite_by_token(202, invite.raw_token)

        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=202, chat_id=202, username='bob', first_name='Bob', last_name=None, language_code='ru')
        )
        pair, inviter, invitee = await self.pairing.accept_invite_by_token(202, invite.raw_token)
        self.assertEqual(pair.created_by_user_id, inviter.id)
        self.assertTrue(pair.includes(inviter.id))
        self.assertTrue(pair.includes(invitee.id))

    async def test_web_only_user_cannot_issue_invite_before_starting_bot(self) -> None:
        await self.users.touch_web_user(
            TelegramUserSnapshot(telegram_user_id=303, chat_id=None, username='webonly', first_name='Web', last_name='Only', language_code='ru')
        )

        with self.assertRaises(ConflictError):
            await self.pairing.create_invite(303)

    async def test_unpair_cancels_pending_care_dispatches(self) -> None:
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=101, chat_id=101, username='alice', first_name='Alice', last_name=None, language_code='ru')
        )
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=202, chat_id=202, username='bob', first_name='Bob', last_name=None, language_code='ru')
        )

        invite = await self.pairing.create_invite(101)
        await self.pairing.accept_invite_by_token(202, invite.raw_token)

        templates = await self.care.list_templates(telegram_user_id=101)
        queued = await self.care.queue_template(telegram_user_id=101, template_code=templates[0].template_code)
        self.assertEqual(queued.dispatch.status, CareDispatchStatus.PENDING)

        await self.pairing.unpair(101)
        refreshed = await self.care_repo.get_dispatch(queued.dispatch.id)
        self.assertIsNotNone(refreshed)
        assert refreshed is not None
        self.assertEqual(refreshed.status, CareDispatchStatus.FAILED)
        self.assertEqual(refreshed.last_error, 'Пара завершена до доставки.')
    async def test_inviter_can_cancel_pending_outgoing_invite_after_preview_binding(self) -> None:
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=101, chat_id=101, username='alice', first_name='Alice', last_name=None, language_code='ru')
        )
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=202, chat_id=202, username='bob', first_name='Bob', last_name=None, language_code='ru')
        )

        invite = await self.pairing.create_invite(101)
        await self.pairing.preview_invite(202, invite.raw_token)

        cancel_invite = getattr(self.pairing, 'cancel_outgoing_invite', None)
        self.assertIsNotNone(cancel_invite, 'PairingService.cancel_outgoing_invite must exist.')
        if cancel_invite is None:
            return

        await cancel_invite(101)

        inviter_dashboard = await self.pairing.get_dashboard(101)
        invitee_dashboard = await self.pairing.get_dashboard(202)

        self.assertEqual(inviter_dashboard.mode, 'no_pair')
        self.assertIsNone(inviter_dashboard.outgoing_invite)
        self.assertEqual(invitee_dashboard.mode, 'no_pair')
        self.assertIsNone(invitee_dashboard.incoming_invite)
        with self.assertRaises(NotFoundError):
            await self.pairing.accept_invite_by_token(202, invite.raw_token)

    async def test_outgoing_invite_dashboard_exposes_cancel_action(self) -> None:
        await self.users.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=101, chat_id=101, username='alice', first_name='Alice', last_name=None, language_code='ru')
        )

        await self.pairing.create_invite(101)
        dashboard = await self.pairing.get_dashboard(101)
        markup = dashboard_keyboard(dashboard, self.settings.webapp_public_url)
        callback_data = [
            button.callback_data
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data is not None
        ]

        self.assertEqual(dashboard.mode, 'outgoing_invite')
        self.assertIn('pair:create', callback_data)
        self.assertIn('invite:cancel_outgoing', callback_data)


if __name__ == '__main__':
    unittest.main()

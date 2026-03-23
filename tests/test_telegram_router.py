from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from nimarita.config import Settings
from nimarita.domain.models import TelegramUserSnapshot
from nimarita.infra import LinkBuilder, SQLiteDatabase
from nimarita.repositories import CareRepository, PairingRepository, ReminderRepository, UserRepository
from nimarita.telegram.keyboards import care_actions_keyboard
from nimarita.services import CareService, PairingService, ReminderService, UserService
from nimarita.telegram.router import build_router


class _NotifierStub:
    pass


class _BotStub:
    async def set_chat_menu_button(self, *, chat_id: int | None = None, menu_button: object | None = None) -> None:
        del chat_id, menu_button


@dataclass(slots=True)
class _EphemeralNotice:
    chat_id: int
    text: str
    kind: str


@dataclass(slots=True)
class _DashboardRender:
    user_id: int
    chat_id: int
    text: str


class _UIStub:
    def __init__(self) -> None:
        self.bot = _BotStub()
        self.ephemeral: list[_EphemeralNotice] = []
        self.dashboards: list[_DashboardRender] = []

    async def send_ephemeral(
        self,
        *,
        chat_id: int,
        text: str,
        seconds: int,
        kind: str,
        reply_markup: object | None = None,
    ) -> int:
        del seconds, reply_markup
        self.ephemeral.append(_EphemeralNotice(chat_id=chat_id, text=text, kind=kind))
        return len(self.ephemeral)

    async def upsert_dashboard(
        self,
        *,
        user_id: int,
        chat_id: int,
        text: str,
        reply_markup: object | None,
    ) -> int:
        del reply_markup
        self.dashboards.append(_DashboardRender(user_id=user_id, chat_id=chat_id, text=text))
        return len(self.dashboards)


@dataclass(slots=True)
class _ChatStub:
    id: int
    type: str


@dataclass(slots=True)
class _FromUserStub:
    id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None


class _MessageStub:
    def __init__(self, *, telegram_user_id: int, chat_id: int, username: str, first_name: str) -> None:
        self.chat = _ChatStub(id=chat_id, type='private')
        self.from_user = _FromUserStub(
            id=telegram_user_id,
            username=username,
            first_name=first_name,
            last_name=None,
            language_code='ru',
        )
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs: object) -> None:
        del kwargs
        self.answers.append(text)


class TelegramRouterStabilisationTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / 'test.db'
        self.db = SQLiteDatabase(db_path)
        await self.db.connect()

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
        self.user_repo = UserRepository(self.db, default_timezone='Europe/Moscow')
        self.pairing_repo = PairingRepository(self.db)
        self.reminder_repo = ReminderRepository(self.db)
        self.care_repo = CareRepository(self.db)

        self.user_service = UserService(self.user_repo)
        self.pairing_service = PairingService(
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=self.settings,
            links=LinkBuilder(self.settings),
            reminders=self.reminder_repo,
            care=self.care_repo,
        )
        self.reminder_service = ReminderService(
            reminders=self.reminder_repo,
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=self.settings,
        )
        self.care_service = CareService(
            care=self.care_repo,
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=self.settings,
        )
        await self.care_service.ensure_seeded()

    async def asyncTearDown(self) -> None:
        await self.db.close()
        self._tmp.cleanup()

    async def test_start_with_invite_conflict_returns_transient_error_instead_of_crashing(self) -> None:
        await self.user_service.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=101, chat_id=101, username='alice', first_name='Alice', last_name=None, language_code='ru')
        )
        await self.user_service.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=202, chat_id=202, username='bob', first_name='Bob', last_name=None, language_code='ru')
        )
        await self.user_service.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=303, chat_id=303, username='carol', first_name='Carol', last_name=None, language_code='ru')
        )

        active_pair_invite = await self.pairing_service.create_invite(101)
        await self.pairing_service.accept_invite_by_token(202, active_pair_invite.raw_token)
        conflicting_invite = await self.pairing_service.create_invite(303)

        ui = _UIStub()
        router = build_router(
            settings=self.settings,
            user_service=self.user_service,
            pairing_service=self.pairing_service,
            reminder_service=self.reminder_service,
            care_service=self.care_service,
            notifier=_NotifierStub(),
            ui=ui,
        )
        command_start = next(handler.callback for handler in router.message.handlers if handler.callback.__name__ == 'command_start')
        message = _MessageStub(telegram_user_id=202, chat_id=202, username='bob', first_name='Bob')

        await command_start(message, command=SimpleNamespace(args=f'invite_{conflicting_invite.raw_token}'))

        invite_preview_errors = [notice for notice in ui.ephemeral if notice.kind == 'invite-preview-error']
        self.assertEqual(len(invite_preview_errors), 1)
        self.assertIn('активная пара', invite_preview_errors[0].text.lower())
        self.assertEqual(len(ui.dashboards), 1)


class TelegramCareKeyboardTestCase(unittest.TestCase):
    def test_custom_care_keyboard_first_page_uses_soft_reply_buttons(self) -> None:
        markup = care_actions_keyboard(
            dispatch_id=77,
            category='custom',
            page=0,
            app_link='https://example.com/app',
        )

        reply_texts = [
            button.text
            for row in markup.inline_keyboard
            for button in row
            if button.callback_data and button.callback_data.startswith('care:reply:')
        ]

        self.assertEqual(
            reply_texts,
            ['💖 Спасибо 💖', '🌿 Очень вовремя 🌿', '🤍 Стало спокойнее 🤍'],
        )
        self.assertFalse(any('Люблю тебя' in text or 'поцелуй' in text for text in reply_texts))


if __name__ == '__main__':
    unittest.main()

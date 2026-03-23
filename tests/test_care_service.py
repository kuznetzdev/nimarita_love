from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from nimarita.catalog import CARE_QUICK_REPLY_DEFINITIONS, CARE_TEMPLATE_DEFINITIONS, get_quick_reply_pages
from nimarita.config import Settings
from nimarita.domain.enums import RelationshipRole
from nimarita.domain.errors import ConflictError
from nimarita.domain.models import TelegramUserSnapshot
from nimarita.infra import LinkBuilder, SQLiteDatabase
from nimarita.repositories import CareRepository, PairingRepository, ReminderRepository, UserRepository
from nimarita.services import CareService, PairingService, UserService
from nimarita.telegram.texts import care_delivery_text, care_reply_applied_text, care_sender_response_text

PARENTHETICAL_GENDER_FORM_RE = re.compile(r'[А-Яа-яЁё]+\([^)\s]{1,20}\)')
NEUTRAL_GENDER_BREAKERS = (
    'живой',
    'живая',
    'сытый',
    'сытая',
    'устал',
    'устала',
    'замёрз',
    'замёрзла',
    'загонял',
    'загоняла',
    'оставался',
    'оставалась',
)
NEUTRAL_GENDER_BREAKER_PATTERNS = tuple(re.compile(rf'\b{re.escape(token)}\b') for token in NEUTRAL_GENDER_BREAKERS)


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

    async def test_role_specific_catalog_is_filtered_for_sender_and_partner(self) -> None:
        await self.users.set_relationship_role(101, RelationshipRole.MAN)
        await self.users.set_relationship_role(202, RelationshipRole.WOMAN)

        man_templates = await self.care.list_templates(telegram_user_id=101)
        woman_templates = await self.care.list_templates(telegram_user_id=202)

        self.assertTrue(any(item.category == 'man_to_woman' for item in man_templates))
        self.assertFalse(any(item.category == 'woman_to_man' for item in man_templates))
        self.assertTrue(any(item.category == 'woman_to_man' for item in woman_templates))
        self.assertFalse(any(item.category == 'man_to_woman' for item in woman_templates))

    async def test_care_templates_and_quick_replies_do_not_use_parenthetical_gender_forms(self) -> None:
        for template in CARE_TEMPLATE_DEFINITIONS:
            haystack = f"{template.title} {template.body}"
            self.assertNotRegex(haystack, PARENTHETICAL_GENDER_FORM_RE)
        for reply in CARE_QUICK_REPLY_DEFINITIONS:
            haystack = f"{reply.title} {reply.body}"
            self.assertNotRegex(haystack, PARENTHETICAL_GENDER_FORM_RE)

    async def test_care_catalog_covers_support_warmth_care_and_short_messages(self) -> None:
        categories = {template.category for template in CARE_TEMPLATE_DEFINITIONS}
        self.assertTrue({'support', 'warmth', 'care', 'short'}.issubset(categories))

    async def test_neutral_templates_and_replies_avoid_gendered_breakers(self) -> None:
        for template in CARE_TEMPLATE_DEFINITIONS:
            if template.recipient_role is not RelationshipRole.UNSPECIFIED:
                continue
            haystack = f'{template.title} {template.body}'.lower()
            for pattern in NEUTRAL_GENDER_BREAKER_PATTERNS:
                self.assertIsNone(pattern.search(haystack))

        for reply in CARE_QUICK_REPLY_DEFINITIONS:
            haystack = f'{reply.title} {reply.body}'.lower()
            for pattern in NEUTRAL_GENDER_BREAKER_PATTERNS:
                self.assertIsNone(pattern.search(haystack))

    async def test_generic_quick_reply_first_page_stays_soft_for_custom_messages(self) -> None:
        pages = get_quick_reply_pages('custom')
        self.assertTrue(pages)
        first_page_codes = [item.code for item in pages[0]]
        self.assertEqual(first_page_codes, ['thanks_love', 'very_timely', 'became_quieter'])

    async def test_telegram_care_texts_render_naturally_without_parenthetical_gender_forms(self) -> None:
        await self.users.set_relationship_role(101, RelationshipRole.MAN)
        await self.users.set_relationship_role(202, RelationshipRole.WOMAN)
        templates = await self.care.list_templates(telegram_user_id=101)
        chosen = next(item for item in templates if item.category == 'man_to_woman')

        queued = await self.care.queue_template(telegram_user_id=101, template_code=chosen.template_code)
        sent = await self.care.mark_sent(dispatch_id=queued.dispatch.id, telegram_message_id=777)
        replied = await self.care.register_quick_reply(
            telegram_user_id=202,
            dispatch_id=sent.dispatch.id,
            reply_code='thanks_love',
        )

        delivery_text = care_delivery_text(sent)
        reply_applied_text = care_reply_applied_text(replied)
        sender_response_text = care_sender_response_text(replied)

        self.assertIn('Alice', delivery_text)
        self.assertIn('Alice', reply_applied_text)
        self.assertIn('Bob', sender_response_text)

        for text in (
            delivery_text,
            reply_applied_text,
            sender_response_text,
        ):
            self.assertNotRegex(text, PARENTHETICAL_GENDER_FORM_RE)
            self.assertTrue(text.strip())

    async def test_custom_care_and_custom_reply_flow(self) -> None:
        sent = await self.care.queue_custom(
            telegram_user_id=101,
            title='Только для тебя',
            body='Напоминаю тебе поесть и немного выдохнуть.',
            emoji='💌',
        )

        self.assertEqual(sent.dispatch.template_code, 'custom')
        self.assertEqual(sent.dispatch.title, 'Только для тебя')

        sent = await self.care.mark_sent(dispatch_id=sent.dispatch.id, telegram_message_id=2024)

        replied = await self.care.register_custom_reply(
            telegram_user_id=202,
            dispatch_id=sent.dispatch.id,
            title='Услышала тебя',
            body='Спасибо, сейчас как раз сделаю паузу.',
            emoji='💗',
        )

        self.assertEqual(replied.envelope.dispatch.status.value, 'responded')
        self.assertEqual(replied.reply.code, 'custom')
        self.assertEqual(replied.reply.title, 'Услышала тебя')


    async def test_duplicate_custom_care_submit_reuses_recent_dispatch(self) -> None:
        first = await self.care.queue_custom(
            telegram_user_id=101,
            title='Quiet nudge',
            body='Drink some water and take a short pause.',
            emoji='hug',
        )
        second = await self.care.queue_custom(
            telegram_user_id=101,
            title='Quiet nudge',
            body='Drink some water and take a short pause.',
            emoji='hug',
        )

        self.assertEqual(second.dispatch.id, first.dispatch.id)
        history = await self.care.list_history(telegram_user_id=101)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].dispatch.id, first.dispatch.id)


if __name__ == '__main__':
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path

from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web_app import NotAppKeyWarning

from nimarita.config import Settings
from nimarita.domain.enums import RelationshipRole
from nimarita.domain.models import TelegramUserSnapshot
from nimarita.infra import LinkBuilder, SQLiteDatabase
from nimarita.repositories import AuditRepository, CareRepository, PairingRepository, ReminderRepository, UserRepository
from nimarita.services import AuditService, CareService, HeartbeatRegistry, PairingService, ReminderService, SystemService, UserService
from nimarita.web.server import WebServer


class _NotifierStub:
    async def notify_pair_confirmed(self, inviter: object, invitee: object) -> None:
        del inviter, invitee

    async def notify_pair_rejected(self, inviter: object, rejector: object) -> None:
        del inviter, rejector

    async def notify_pair_closed(self, actor: object, partner: object) -> None:
        del actor, partner

    async def notify_care_response(self, result: object) -> None:
        del result


class _VerifierStub:
    def __init__(self, snapshot: TelegramUserSnapshot) -> None:
        self._snapshot = snapshot

    def verify(self, init_data: str) -> TelegramUserSnapshot:
        del init_data
        return self._snapshot


class WebServerContractTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / 'test.db'
        self.db = SQLiteDatabase(db_path)
        await self.db.connect()

        self.user_repo = UserRepository(self.db, default_timezone='Europe/Moscow')
        self.pairing_repo = PairingRepository(self.db)
        self.reminder_repo = ReminderRepository(self.db)
        self.care_repo = CareRepository(self.db)
        self.audit_repo = AuditRepository(self.db)
        self.audit = AuditService(self.audit_repo)
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
        self.user_service = UserService(self.user_repo, audit=self.audit)
        self.pairing_service = PairingService(
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=self.settings,
            links=LinkBuilder(self.settings),
            reminders=self.reminder_repo,
            care=self.care_repo,
            audit=self.audit,
        )
        self.reminder_service = ReminderService(
            reminders=self.reminder_repo,
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=self.settings,
            audit=self.audit,
        )
        self.care_service = CareService(
            care=self.care_repo,
            pairing=self.pairing_repo,
            users=self.user_repo,
            settings=self.settings,
            audit=self.audit,
        )
        await self.care_service.ensure_seeded()
        self.system_service = SystemService(
            settings=self.settings,
            database=self.db,
            pairing=self.pairing_repo,
            reminders=self.reminder_repo,
            care=self.care_repo,
            heartbeats=HeartbeatRegistry(),
            audit=self.audit,
        )
        self.web_server = WebServer(
            settings=self.settings,
            user_service=self.user_service,
            pairing_service=self.pairing_service,
            reminder_service=self.reminder_service,
            care_service=self.care_service,
            notifier=_NotifierStub(),
            audit=self.audit,
            system=self.system_service,
        )
        self.http_server = TestServer(self.web_server._app)
        self.client = TestClient(self.http_server)
        await self.client.start_server()

    async def asyncTearDown(self) -> None:
        await self.client.close()
        await self.db.close()
        self._tmp.cleanup()

    async def test_auth_reloads_dashboard_after_invite_preview_binding(self) -> None:
        await self._ensure_bot_user(101, 101, 'alice', 'Alice')
        await self._ensure_bot_user(202, 202, 'bob', 'Bob')
        invite = await self.pairing_service.create_invite(101)

        payload = await self._auth(
            TelegramUserSnapshot(telegram_user_id=202, chat_id=None, username='bob', first_name='Bob', last_name=None, language_code='ru'),
            start_param=f'invite_{invite.raw_token}',
        )

        state = payload.get('state')
        self.assertIsInstance(state, dict)
        assert isinstance(state, dict)
        self.assertEqual(state.get('mode'), 'incoming_invite')
        incoming_invite = state.get('incoming_invite')
        self.assertIsInstance(incoming_invite, dict)
        assert isinstance(incoming_invite, dict)
        self.assertEqual(incoming_invite.get('id'), invite.invite.id)

    async def test_auth_exposes_invite_preview_error_for_missing_invite(self) -> None:
        payload = await self._auth(
            TelegramUserSnapshot(telegram_user_id=303, chat_id=None, username='webonly', first_name='Web', last_name='Only', language_code='ru'),
            start_param='invite_missing-token',
        )

        self.assertIn('invite_preview_error', payload)
        self.assertIsNone(payload.get('invite_preview'))
        self.assertIsInstance(payload.get('invite_preview_error'), str)
        self.assertTrue(str(payload['invite_preview_error']).strip())

    async def test_auth_rejects_non_object_json_payload(self) -> None:
        response = await self.client.post(
            '/api/v1/auth',
            data='[]',
            headers={'Content-Type': 'application/json'},
        )
        self.assertEqual(response.status, 400)
        payload = await response.json()
        self.assertFalse(payload.get('ok', True))
        self.assertIsInstance(payload.get('error'), str)
        self.assertTrue(str(payload.get('error')).strip())

    async def test_reject_invite_returns_full_dashboard_payload_shape(self) -> None:
        await self._ensure_bot_user(101, 101, 'alice', 'Alice')
        await self._ensure_bot_user(202, 202, 'bob', 'Bob')
        invite = await self.pairing_service.create_invite(101)
        await self.pairing_service.preview_invite(202, invite.raw_token)

        response = await self.client.post(
            '/api/v1/pairs/reject',
            json={'invite_id': invite.invite.id},
            headers=self._auth_headers(202),
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()

        self._assert_full_dashboard_payload(payload)
        self.assertEqual(payload['state']['mode'], 'no_pair')

    async def test_unpair_returns_full_dashboard_payload_shape(self) -> None:
        await self._ensure_bot_user(101, 101, 'alice', 'Alice')
        await self._ensure_bot_user(202, 202, 'bob', 'Bob')
        invite = await self.pairing_service.create_invite(101)
        await self.pairing_service.accept_invite_by_token(202, invite.raw_token)

        response = await self.client.post(
            '/api/v1/pairs/unpair',
            json={},
            headers=self._auth_headers(101),
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()

        self._assert_full_dashboard_payload(payload)
        self.assertEqual(payload['state']['mode'], 'no_pair')

    async def test_care_history_exposes_quick_replies_and_reply_endpoint_accepts_reply_code(self) -> None:
        await self._ensure_bot_user(101, 101, 'alice', 'Alice')
        await self._ensure_bot_user(202, 202, 'bob', 'Bob')
        invite = await self.pairing_service.create_invite(101)
        await self.pairing_service.accept_invite_by_token(202, invite.raw_token)

        templates = await self.care_service.list_templates(telegram_user_id=101)
        await self.care_service.send_template_now(
            telegram_user_id=101,
            template_code=templates[0].template_code,
            deliver=self._deliver_care_message,
        )

        history_response = await self.client.get('/api/v1/care/history', headers=self._auth_headers(202))
        self.assertEqual(history_response.status, 200)
        history_payload = await history_response.json()
        history = history_payload.get('history')
        self.assertIsInstance(history, list)
        assert isinstance(history, list)
        self.assertTrue(history)

        inbound_item = next(item for item in history if item.get('direction') == 'inbound')
        self.assertIn('quick_replies', inbound_item)
        quick_replies = inbound_item.get('quick_replies')
        self.assertIsInstance(quick_replies, list)
        assert isinstance(quick_replies, list)
        self.assertTrue(any(isinstance(reply, dict) and reply.get('code') == 'thanks_love' for reply in quick_replies))

        respond_response = await self.client.post(
            '/api/v1/care/respond',
            json={'dispatch_id': inbound_item['id'], 'reply_code': 'thanks_love'},
            headers=self._auth_headers(202),
        )
        self.assertEqual(respond_response.status, 200)
        respond_payload = await respond_response.json()
        dispatch = respond_payload.get('dispatch')
        self.assertIsInstance(dispatch, dict)
        assert isinstance(dispatch, dict)
        self.assertEqual(dispatch.get('response_code'), 'thanks_love')
        self.assertEqual(dispatch.get('status'), 'responded')

    async def test_build_app_does_not_emit_not_app_key_warning(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always', NotAppKeyWarning)
            WebServer(
                settings=self.settings,
                user_service=self.user_service,
                pairing_service=self.pairing_service,
                reminder_service=self.reminder_service,
                care_service=self.care_service,
                notifier=_NotifierStub(),
                audit=self.audit,
                system=self.system_service,
            )

        app_key_warnings = [warning for warning in caught if issubclass(warning.category, NotAppKeyWarning)]
        self.assertEqual(app_key_warnings, [])

    async def test_health_ready_exposes_deployment_checks(self) -> None:
        response = await self.client.get('/health/ready')
        self.assertEqual(response.status, 200)
        payload = await response.json()
        checks = payload.get('checks')
        self.assertIsInstance(checks, dict)
        assert isinstance(checks, dict)
        deployment = checks.get('deployment')
        self.assertIsInstance(deployment, dict)
        assert isinstance(deployment, dict)
        self.assertEqual(deployment.get('sqlite_journal_mode'), self.settings.sqlite_journal_mode)
        self.assertEqual(deployment.get('database_path'), str(self.settings.database_path))
        self.assertIsInstance(deployment.get('warnings'), list)

    async def test_frontend_api_base_uses_public_origin_not_mini_app_path(self) -> None:
        html = self.web_server._get_frontend_html()

        self.assertIsInstance(html, str)
        assert isinstance(html, str)
        self.assertIn("const RAW_API_BASE = 'https://example.com';", html)
        self.assertNotIn("const RAW_API_BASE = 'https://example.com/app';", html)

    async def test_frontend_html_exposes_compact_navigation_and_readable_invite_copy(self) -> None:
        html = self.web_server._get_frontend_html()

        self.assertIsInstance(html, str)
        assert isinstance(html, str)
        for fragment in (
            'id="jump-reminders"',
            'id="jump-messages"',
            'id="toggle-reminder-form"',
            'id="toggle-custom-care"',
            'id="toggle-care-history"',
        ):
            self.assertIn(fragment, html)
        self.assertIn('Отменить приглашение', html)
        self.assertNotIn('РћС‚РјРµРЅРёС‚СЊ', html)

    async def test_frontend_html_exposes_quiet_secondary_controls_and_sticky_tabs(self) -> None:
        html = self.web_server._get_frontend_html()

        self.assertIsInstance(html, str)
        assert isinstance(html, str)
        for fragment in (
            'sticky-tabs',
            'id="active-secondary-actions"',
            'id="refresh-state" class="ghost text-button small-button"',
            'id="toggle-pair-actions" class="ghost text-button small-button"',
            "toggleActionsButton.id = 'toggle-invite-actions';",
            'id="care-history-refresh" class="secondary hidden-space small-button"',
        ):
            self.assertIn(fragment, html)

    async def test_frontend_html_exposes_mobile_safe_tab_labels(self) -> None:
        html = self.web_server._get_frontend_html()

        self.assertIsInstance(html, str)
        assert isinstance(html, str)
        self.assertEqual(html.count('class="tab-button-label"'), 3)
        for fragment in (
            'id="tab-pair"',
            'id="tab-reminders"',
            'id="tab-messages"',
            '.tab-button-label {',
            'overflow-wrap: anywhere;',
            'word-break: break-word;',
            'hyphens: auto;',
        ):
            self.assertIn(fragment, html)

    async def test_frontend_html_exposes_responsive_field_and_toolbar_rules(self) -> None:
        html = self.web_server._get_frontend_html()

        self.assertIsInstance(html, str)
        assert isinstance(html, str)
        for fragment in (
            '.field-flex-92 { flex: 0 0 92px; }',
            '.field-flex-220 { flex: 1 1 220px; }',
            '.field-flex-260 { flex: 1 1 260px; }',
            '@media (max-width: 720px) {',
            '.header-row > .toolbar,',
            '.quiet-actions > *,',
            '.actions,',
            '.choice-row,',
            '.copy-actions {',
            'grid-template-columns: minmax(0, 1fr);',
        ):
            self.assertIn(fragment, html)

    async def test_frontend_html_exposes_clear_reminder_edit_copy_and_history_disclosure(self) -> None:
        html = self.web_server._get_frontend_html()

        self.assertIsInstance(html, str)
        assert isinstance(html, str)
        for fragment in (
            'Отменить редактирование',
            'Показать всю историю',
            'Показать меньше',
            'Регулярность:',
        ):
            self.assertIn(fragment, html)

    async def test_frontend_html_exposes_care_preview_direction_and_tone_copy(self) -> None:
        html = self.web_server._get_frontend_html()

        self.assertIsInstance(html, str)
        assert isinstance(html, str)
        for fragment in (
            'Что сейчас отправится',
            'Кому уйдёт',
            'Тон сообщения',
            'Ответ уйдёт',
            'Как ответить сейчас',
        ):
            self.assertIn(fragment, html)

    async def test_care_templates_api_exposes_tone_and_recipient_metadata(self) -> None:
        await self._ensure_bot_user(101, 101, 'alice', 'Alice')
        await self._ensure_bot_user(202, 202, 'bob', 'Bob')
        invite = await self.pairing_service.create_invite(101)
        await self.pairing_service.accept_invite_by_token(202, invite.raw_token)
        await self.user_service.set_relationship_role(101, RelationshipRole.MAN)
        await self.user_service.set_relationship_role(202, RelationshipRole.WOMAN)

        response = await self.client.get('/api/v1/care/templates', headers=self._auth_headers(101))
        self.assertEqual(response.status, 200)
        payload = await response.json()
        templates = payload.get('templates')
        self.assertIsInstance(templates, list)
        assert isinstance(templates, list)
        self.assertTrue(templates)

        role_aware = next(item for item in templates if isinstance(item, dict) and item.get('category') == 'man_to_woman')
        self.assertEqual(role_aware.get('recipient_hint'), 'адресовано девушке')
        self.assertTrue(str(role_aware.get('tone_label', '')).strip())

    async def test_reminder_api_roundtrip_preserves_recurrence_fields_and_cancel_state(self) -> None:
        await self._ensure_bot_user(101, 101, 'alice', 'Alice')
        await self._ensure_bot_user(202, 202, 'bob', 'Bob')
        invite = await self.pairing_service.create_invite(101)
        await self.pairing_service.accept_invite_by_token(202, invite.raw_token)

        create_response = await self.client.post(
            '/api/v1/reminders',
            json={
                'text': 'Hydrate together',
                'scheduled_for_local': '2030-01-01T10:00',
                'timezone': 'Europe/Moscow',
                'kind': 'interval',
                'recurrence_every': 6,
                'recurrence_unit': 'hour',
            },
            headers=self._auth_headers(101),
        )
        self.assertEqual(create_response.status, 201)
        create_payload = await create_response.json()
        created = create_payload.get('reminder')
        self.assertIsInstance(created, dict)
        assert isinstance(created, dict)
        self.assertEqual(created.get('kind'), 'interval')
        self.assertEqual(created.get('recurrence_every'), 6)
        self.assertEqual(created.get('recurrence_unit'), 'hour')
        self.assertTrue(str(created.get('kind_label', '')).strip())

        rule_id = created['rule_id']
        update_response = await self.client.post(
            f'/api/v1/reminders/{rule_id}',
            json={
                'text': 'Weekly rhythm',
                'scheduled_for_local': '2030-01-05T09:30',
                'timezone': 'Europe/Moscow',
                'kind': 'weekly',
                'recurrence_every': 9,
                'recurrence_unit': 'month',
            },
            headers=self._auth_headers(101),
        )
        self.assertEqual(update_response.status, 200)
        update_payload = await update_response.json()
        updated = update_payload.get('reminder')
        self.assertIsInstance(updated, dict)
        assert isinstance(updated, dict)
        self.assertEqual(updated.get('kind'), 'weekly')
        self.assertEqual(updated.get('recurrence_every'), 1)
        self.assertEqual(updated.get('recurrence_unit'), 'week')
        self.assertTrue(str(updated.get('kind_label', '')).strip())

        cancel_response = await self.client.post(
            f'/api/v1/reminders/{rule_id}/cancel',
            json={},
            headers=self._auth_headers(101),
        )
        self.assertEqual(cancel_response.status, 200)
        cancel_payload = await cancel_response.json()
        cancelled = cancel_payload.get('reminder')
        self.assertIsInstance(cancelled, dict)
        assert isinstance(cancelled, dict)
        self.assertEqual(cancelled.get('rule_status'), 'cancelled')
        self.assertEqual(cancelled.get('status'), 'cancelled')
        reminders = cancel_payload.get('reminders')
        self.assertIsInstance(reminders, list)
        assert isinstance(reminders, list)
        self.assertFalse(
            any(item.get('rule_id') == rule_id and item.get('status') == 'scheduled' for item in reminders if isinstance(item, dict))
        )

    async def test_reminder_api_rejects_invalid_recurrence_every_values(self) -> None:
        await self._ensure_bot_user(101, 101, 'alice', 'Alice')
        await self._ensure_bot_user(202, 202, 'bob', 'Bob')
        invite = await self.pairing_service.create_invite(101)
        await self.pairing_service.accept_invite_by_token(202, invite.raw_token)

        for recurrence_every in ('oops', 0):
            with self.subTest(recurrence_every=recurrence_every):
                response = await self.client.post(
                    '/api/v1/reminders',
                    json={
                        'text': 'Bad cadence',
                        'scheduled_for_local': '2030-01-01T10:00',
                        'timezone': 'Europe/Moscow',
                        'kind': 'interval',
                        'recurrence_every': recurrence_every,
                        'recurrence_unit': 'day',
                    },
                    headers=self._auth_headers(101),
                )
                self.assertEqual(response.status, 400)
                payload = await response.json()
                self.assertFalse(payload.get('ok', True))
                self.assertIsInstance(payload.get('error'), str)
                self.assertTrue(str(payload.get('error')).strip())

    async def test_reminder_api_forbids_non_creator_edit_and_cancel(self) -> None:
        await self._ensure_bot_user(101, 101, 'alice', 'Alice')
        await self._ensure_bot_user(202, 202, 'bob', 'Bob')
        invite = await self.pairing_service.create_invite(101)
        await self.pairing_service.accept_invite_by_token(202, invite.raw_token)

        create_response = await self.client.post(
            '/api/v1/reminders',
            json={
                'text': 'Creator-only reminder',
                'scheduled_for_local': '2030-01-01T10:00',
                'timezone': 'Europe/Moscow',
                'kind': 'one_time',
            },
            headers=self._auth_headers(101),
        )
        self.assertEqual(create_response.status, 201)
        created = await create_response.json()
        reminder = created.get('reminder')
        self.assertIsInstance(reminder, dict)
        assert isinstance(reminder, dict)
        rule_id = reminder['rule_id']

        update_response = await self.client.post(
            f'/api/v1/reminders/{rule_id}',
            json={
                'text': 'Tampered',
                'scheduled_for_local': '2030-01-02T10:00',
                'timezone': 'Europe/Moscow',
                'kind': 'daily',
            },
            headers=self._auth_headers(202),
        )
        self.assertEqual(update_response.status, 409)

        cancel_response = await self.client.post(
            f'/api/v1/reminders/{rule_id}/cancel',
            json={},
            headers=self._auth_headers(202),
        )
        self.assertEqual(cancel_response.status, 409)

        list_response = await self.client.get('/api/v1/reminders', headers=self._auth_headers(101))
        self.assertEqual(list_response.status, 200)
        list_payload = await list_response.json()
        reminders = list_payload.get('reminders')
        self.assertIsInstance(reminders, list)
        assert isinstance(reminders, list)
        active = next(item for item in reminders if isinstance(item, dict) and item.get('rule_id') == rule_id)
        self.assertEqual(active.get('rule_status'), 'active')
        self.assertEqual(active.get('status'), 'scheduled')

    async def test_care_reply_validation_errors_are_readable(self) -> None:
        await self._ensure_bot_user(999, 999, 'ghost', 'Ghost')

        missing_dispatch_response = await self.client.post(
            '/api/v1/care/respond',
            json={},
            headers=self._auth_headers(999),
        )
        self.assertEqual(missing_dispatch_response.status, 400)
        missing_dispatch_payload = await missing_dispatch_response.json()
        self.assertEqual(missing_dispatch_payload.get('error'), 'Нужен dispatch_id для ответа.')

        missing_reply_code_response = await self.client.post(
            '/api/v1/care/respond',
            json={'dispatch_id': 1},
            headers=self._auth_headers(999),
        )
        self.assertEqual(missing_reply_code_response.status, 400)
        missing_reply_code_payload = await missing_reply_code_response.json()
        self.assertEqual(missing_reply_code_payload.get('error'), 'Нужен reply_code для ответа.')

    async def _auth(self, snapshot: TelegramUserSnapshot, *, start_param: str | None = None) -> dict[str, object]:
        self.web_server._verifier = _VerifierStub(snapshot)
        response = await self.client.post(
            '/api/v1/auth',
            json={'init_data': 'stub-init-data', 'start_param': start_param},
        )
        self.assertEqual(response.status, 200)
        payload = await response.json()
        self.assertIsInstance(payload, dict)
        return payload

    async def _ensure_bot_user(self, telegram_user_id: int, chat_id: int, username: str, first_name: str) -> None:
        await self.user_service.ensure_bot_user(
            TelegramUserSnapshot(
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                username=username,
                first_name=first_name,
                last_name=None,
                language_code='ru',
            )
        )

    async def _deliver_care_message(self, envelope: object) -> int:
        del envelope
        return 777

    def _auth_headers(self, telegram_user_id: int) -> dict[str, str]:
        token = self.web_server._sessions.issue(telegram_user_id=telegram_user_id)
        return {'Authorization': f'Bearer {token}'}

    def _assert_full_dashboard_payload(self, payload: dict[str, object]) -> None:
        self.assertIn('state', payload)
        for key in ('reminders', 'care_templates', 'care_history'):
            self.assertIn(key, payload)
            self.assertIsInstance(payload[key], list)


if __name__ == '__main__':
    unittest.main()

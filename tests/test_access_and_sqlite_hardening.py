from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nimarita.config import Settings, load_settings
from nimarita.domain.errors import AccessDeniedError
from nimarita.domain.models import TelegramUserSnapshot
from nimarita.infra import SQLiteDatabase
from nimarita.repositories import AuditRepository, CareRepository, PairingRepository, ReminderRepository, UserRepository
from nimarita.services import AccessPolicy, AuditService, HeartbeatRegistry, SystemService, UserService


class AccessAndSqliteHardeningTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / 'test.db'

    async def asyncTearDown(self) -> None:
        self._tmp.cleanup()

    async def test_allowlist_feature_flag_blocks_unknown_user(self) -> None:
        db = SQLiteDatabase(self.db_path)
        await db.connect()
        repo = UserRepository(db, default_timezone='Europe/Moscow')
        settings = Settings(
            bot_token='123:TEST',
            bot_username='testbot',
            webapp_public_url='https://example.com/app',
            webapp_enabled=True,
            webapp_host='127.0.0.1',
            webapp_port=8080,
            database_path=self.db_path,
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
            access_allowlist_enabled=True,
            allowed_user_ids=frozenset({101}),
        )
        service = UserService(repo, access=AccessPolicy(settings))

        allowed = await service.ensure_bot_user(
            TelegramUserSnapshot(telegram_user_id=101, chat_id=101, username='alice', first_name='Alice', last_name=None, language_code='ru')
        )
        self.assertEqual(allowed.telegram_user_id, 101)

        with self.assertRaises(AccessDeniedError):
            await service.ensure_bot_user(
                TelegramUserSnapshot(telegram_user_id=202, chat_id=202, username='bob', first_name='Bob', last_name=None, language_code='ru')
            )
        await db.close()

    async def test_load_settings_prefers_railway_volume_and_safe_journal_mode(self) -> None:
        volume_path = Path(self._tmp.name) / 'volume'
        env = {
            'BOT_TOKEN': '123:TEST',
            'BOT_USERNAME': 'testbot',
            'WEBAPP_PUBLIC_URL': 'https://example.com/app',
            'APP_SESSION_SECRET': 'secret',
            'RAILWAY_VOLUME_MOUNT_PATH': str(volume_path),
            'SQLITE_JOURNAL_MODE': 'AUTO',
        }
        with patch.dict('os.environ', env, clear=True):
            settings = load_settings()

        self.assertEqual(settings.database_path, volume_path / 'nimarita.db')
        self.assertEqual(settings.backup_directory, volume_path / 'backups')
        self.assertEqual(settings.sqlite_journal_mode, 'DELETE')

    async def test_sqlite_backup_audit_and_active_pair_trigger(self) -> None:
        db = SQLiteDatabase(self.db_path, synchronous='FULL')
        await db.connect()
        users = UserRepository(db, default_timezone='Europe/Moscow')
        pairing = PairingRepository(db)
        reminders = ReminderRepository(db)
        care = CareRepository(db)
        audit_repo = AuditRepository(db)
        audit = AuditService(audit_repo)
        system = SystemService(
            settings=Settings(
                bot_token='123:TEST',
                bot_username='testbot',
                webapp_public_url='https://example.com/app',
                webapp_enabled=True,
                webapp_host='127.0.0.1',
                webapp_port=8080,
                database_path=self.db_path,
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
                backup_enabled=True,
                backup_directory=Path(self._tmp.name) / 'backups',
                backup_retention=2,
                sqlite_quick_check_on_startup=True,
            ),
            database=db,
            pairing=pairing,
            reminders=reminders,
            care=care,
            heartbeats=HeartbeatRegistry(),
            audit=audit,
        )

        for telegram_user_id in (101, 202, 303):
            await users.upsert_telegram_user(
                TelegramUserSnapshot(
                    telegram_user_id=telegram_user_id,
                    chat_id=telegram_user_id,
                    username=f'u{telegram_user_id}',
                    first_name=f'U{telegram_user_id}',
                    last_name=None,
                    language_code='ru',
                ),
                started_bot=True,
            )

        audit_snapshot = await system.audit_database(reason='test')
        self.assertTrue(audit_snapshot.ok)

        checkpoint = await system.checkpoint_database(reason='test')
        self.assertIn(checkpoint.mode, {'PASSIVE', 'FULL', 'RESTART', 'TRUNCATE'})

        backup = await system.create_backup(reason='test')
        self.assertTrue(backup.ok)
        self.assertIsNotNone(backup.path)
        self.assertTrue(Path(backup.path or '').exists())

        await db.execute(
            """
            INSERT INTO pairs (
                user_a_id, user_b_id, status, created_by_user_id, confirmed_at, closed_at, created_at, updated_at
            ) VALUES (?, ?, 'active', ?, '2026-01-01T00:00:00+00:00', NULL, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
            """,
            (1, 2, 1),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            await db.execute(
                """
                INSERT INTO pairs (
                    user_a_id, user_b_id, status, created_by_user_id, confirmed_at, closed_at, created_at, updated_at
                ) VALUES (?, ?, 'active', ?, '2026-01-01T00:00:00+00:00', NULL, '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')
                """,
                (1, 3, 1),
            )
        await db.close()


if __name__ == '__main__':
    unittest.main()

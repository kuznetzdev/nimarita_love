from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

load_dotenv()


def _read_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


def _read_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value.strip())


def _read_optional(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    clean = value.strip()
    return clean or None


def _read_user_id_set(name: str) -> frozenset[int]:
    raw = _read_optional(name)
    if raw is None:
        return frozenset()
    values: set[int] = set()
    for chunk in raw.replace('\n', ',').replace(';', ',').split(','):
        clean = chunk.strip()
        if not clean:
            continue
        values.add(int(clean))
    return frozenset(values)


@dataclass(slots=True, frozen=True)
class Settings:
    bot_token: str
    bot_username: str
    webapp_public_url: str | None
    webapp_enabled: bool
    webapp_host: str
    webapp_port: int
    database_path: Path
    log_level: str
    default_timezone: str
    init_data_ttl_seconds: int
    session_ttl_seconds: int
    session_secret: str
    pair_invite_ttl_minutes: int
    mini_app_short_name: str | None
    mini_app_title: str
    reminder_worker_poll_seconds: int
    reminder_batch_size: int
    reminder_max_retries: int
    reminder_retry_base_seconds: int
    cleanup_worker_poll_seconds: int
    cleanup_batch_size: int
    action_message_ttl_seconds: int
    notice_message_ttl_seconds: int
    welcome_message_ttl_seconds: int
    care_per_minute_limit: int
    care_per_hour_limit: int
    care_duplicate_window_minutes: int
    care_history_limit: int
    care_sender_notice_ttl_seconds: int
    reminder_worker_concurrency: int = 4
    care_worker_poll_seconds: int = 3
    care_batch_size: int = 20
    care_max_retries: int = 4
    care_retry_base_seconds: int = 20
    care_worker_concurrency: int = 4
    processing_stale_seconds: int = 180
    worker_heartbeat_stale_seconds: int = 90
    access_allowlist_enabled: bool = False
    allowed_user_ids: frozenset[int] = frozenset()
    sqlite_synchronous: str = 'FULL'
    sqlite_busy_timeout_ms: int = 15000
    sqlite_wal_autocheckpoint_pages: int = 1000
    sqlite_journal_size_limit_bytes: int = 67108864
    sqlite_checkpoint_interval_seconds: int = 300
    sqlite_checkpoint_mode: str = 'PASSIVE'
    sqlite_quick_check_on_startup: bool = True
    sqlite_quick_check_interval_seconds: int = 1800
    sqlite_fail_fast_on_integrity_error: bool = False
    backup_enabled: bool = True
    backup_interval_seconds: int = 3600
    backup_retention: int = 24
    backup_directory: Path = Path('data/backups')
    backup_on_startup: bool = True
    backup_on_shutdown: bool = False
    maintenance_worker_poll_seconds: int = 30

    @property
    def direct_main_app_link(self) -> str | None:
        if not self.bot_username:
            return None
        if self.mini_app_short_name:
            return f'https://t.me/{self.bot_username}/{self.mini_app_short_name}'
        return f'https://t.me/{self.bot_username}?startapp'



def load_settings() -> Settings:
    bot_token = _read_optional('BOT_TOKEN')
    if not bot_token:
        raise RuntimeError('BOT_TOKEN is required for the new product runtime.')

    bot_username = _read_optional('BOT_USERNAME')
    if not bot_username:
        raise RuntimeError('BOT_USERNAME is required for deep links and pair invites.')

    webapp_public_url = _read_optional('WEBAPP_PUBLIC_URL')
    if webapp_public_url is not None:
        parsed = urlparse(webapp_public_url)
        if parsed.scheme != 'https':
            raise RuntimeError('WEBAPP_PUBLIC_URL must use HTTPS.')

    session_secret = _read_optional('APP_SESSION_SECRET') or bot_token
    database_path = Path(_read_optional('PRODUCT_DB_PATH') or 'data/nimarita.db')
    backup_directory = Path(_read_optional('PRODUCT_BACKUP_DIR') or 'data/backups')
    sqlite_synchronous = (_read_optional('SQLITE_SYNCHRONOUS') or 'FULL').strip().upper()
    if sqlite_synchronous not in {'OFF', 'NORMAL', 'FULL', 'EXTRA'}:
        raise RuntimeError('SQLITE_SYNCHRONOUS must be one of OFF, NORMAL, FULL, EXTRA.')
    sqlite_checkpoint_mode = (_read_optional('SQLITE_CHECKPOINT_MODE') or 'PASSIVE').strip().upper()
    if sqlite_checkpoint_mode not in {'PASSIVE', 'FULL', 'RESTART', 'TRUNCATE'}:
        raise RuntimeError('SQLITE_CHECKPOINT_MODE must be PASSIVE, FULL, RESTART or TRUNCATE.')

    return Settings(
        bot_token=bot_token,
        bot_username=bot_username.lstrip('@'),
        webapp_public_url=webapp_public_url,
        webapp_enabled=_read_bool('WEBAPP_ENABLED', True),
        webapp_host=os.getenv('WEBAPP_LISTEN_HOST', '127.0.0.1'),
        webapp_port=_read_int('WEBAPP_LISTEN_PORT', 8080),
        database_path=database_path,
        log_level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        default_timezone=os.getenv('DEFAULT_TIMEZONE', 'Europe/Moscow'),
        init_data_ttl_seconds=_read_int('TELEGRAM_INIT_DATA_TTL_SECONDS', 3600),
        session_ttl_seconds=_read_int('APP_SESSION_TTL_SECONDS', 7200),
        session_secret=session_secret,
        pair_invite_ttl_minutes=_read_int('PAIR_INVITE_TTL_MINUTES', 4320),
        mini_app_short_name=_read_optional('MINI_APP_SHORT_NAME'),
        mini_app_title=os.getenv('MINI_APP_TITLE', 'Наше пространство 💖'),
        reminder_worker_poll_seconds=_read_int('REMINDER_WORKER_POLL_SECONDS', 5),
        reminder_batch_size=_read_int('REMINDER_WORKER_BATCH_SIZE', 20),
        reminder_max_retries=_read_int('REMINDER_MAX_RETRIES', 4),
        reminder_retry_base_seconds=_read_int('REMINDER_RETRY_BASE_SECONDS', 30),
        cleanup_worker_poll_seconds=_read_int('CLEANUP_WORKER_POLL_SECONDS', 8),
        cleanup_batch_size=_read_int('CLEANUP_WORKER_BATCH_SIZE', 25),
        action_message_ttl_seconds=_read_int('ACTION_MESSAGE_TTL_SECONDS', 12),
        notice_message_ttl_seconds=_read_int('NOTICE_MESSAGE_TTL_SECONDS', 20),
        welcome_message_ttl_seconds=_read_int('WELCOME_MESSAGE_TTL_SECONDS', 25),
        care_per_minute_limit=_read_int('CARE_PER_MINUTE_LIMIT', 6),
        care_per_hour_limit=_read_int('CARE_PER_HOUR_LIMIT', 40),
        care_duplicate_window_minutes=_read_int('CARE_DUPLICATE_WINDOW_MINUTES', 20),
        care_history_limit=_read_int('CARE_HISTORY_LIMIT', 60),
        care_sender_notice_ttl_seconds=_read_int('CARE_SENDER_NOTICE_TTL_SECONDS', 24),
        reminder_worker_concurrency=_read_int('REMINDER_WORKER_CONCURRENCY', 4),
        care_worker_poll_seconds=_read_int('CARE_WORKER_POLL_SECONDS', 3),
        care_batch_size=_read_int('CARE_WORKER_BATCH_SIZE', 20),
        care_max_retries=_read_int('CARE_MAX_RETRIES', 4),
        care_retry_base_seconds=_read_int('CARE_RETRY_BASE_SECONDS', 20),
        care_worker_concurrency=_read_int('CARE_WORKER_CONCURRENCY', 4),
        processing_stale_seconds=_read_int('PROCESSING_STALE_SECONDS', 180),
        worker_heartbeat_stale_seconds=_read_int('WORKER_HEARTBEAT_STALE_SECONDS', 90),
        access_allowlist_enabled=_read_bool('ACCESS_ALLOWLIST_ENABLED', False),
        allowed_user_ids=_read_user_id_set('ALLOWED_USER_IDS'),
        sqlite_synchronous=sqlite_synchronous,
        sqlite_busy_timeout_ms=_read_int('SQLITE_BUSY_TIMEOUT_MS', 15000),
        sqlite_wal_autocheckpoint_pages=_read_int('SQLITE_WAL_AUTOCHECKPOINT_PAGES', 1000),
        sqlite_journal_size_limit_bytes=_read_int('SQLITE_JOURNAL_SIZE_LIMIT_BYTES', 67108864),
        sqlite_checkpoint_interval_seconds=_read_int('SQLITE_CHECKPOINT_INTERVAL_SECONDS', 300),
        sqlite_checkpoint_mode=sqlite_checkpoint_mode,
        sqlite_quick_check_on_startup=_read_bool('SQLITE_QUICK_CHECK_ON_STARTUP', True),
        sqlite_quick_check_interval_seconds=_read_int('SQLITE_QUICK_CHECK_INTERVAL_SECONDS', 1800),
        sqlite_fail_fast_on_integrity_error=_read_bool('SQLITE_FAIL_FAST_ON_INTEGRITY_ERROR', False),
        backup_enabled=_read_bool('BACKUP_ENABLED', True),
        backup_interval_seconds=_read_int('BACKUP_INTERVAL_SECONDS', 3600),
        backup_retention=_read_int('BACKUP_RETENTION', 24),
        backup_directory=backup_directory,
        backup_on_startup=_read_bool('BACKUP_ON_STARTUP', True),
        backup_on_shutdown=_read_bool('BACKUP_ON_SHUTDOWN', False),
        maintenance_worker_poll_seconds=_read_int('MAINTENANCE_WORKER_POLL_SECONDS', 30),
    )

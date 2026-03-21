from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_user_id INTEGER NOT NULL UNIQUE,
    private_chat_id INTEGER,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    language_code TEXT,
    timezone TEXT NOT NULL,
    relationship_role TEXT NOT NULL DEFAULT 'unspecified',
    started_bot INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS pair_invites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inviter_user_id INTEGER NOT NULL,
    invitee_user_id INTEGER,
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(inviter_user_id) REFERENCES users(id),
    FOREIGN KEY(invitee_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_a_id INTEGER NOT NULL,
    user_b_id INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_by_user_id INTEGER NOT NULL,
    confirmed_at TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (user_a_id < user_b_id),
    FOREIGN KEY(user_a_id) REFERENCES users(id),
    FOREIGN KEY(user_b_id) REFERENCES users(id),
    FOREIGN KEY(created_by_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS reminder_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id INTEGER NOT NULL,
    creator_user_id INTEGER NOT NULL,
    recipient_user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    text TEXT NOT NULL,
    creator_timezone TEXT NOT NULL,
    origin_scheduled_at_utc TEXT NOT NULL,
    recurrence_every INTEGER NOT NULL DEFAULT 1,
    recurrence_unit TEXT,
    status TEXT NOT NULL,
    cancelled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(pair_id) REFERENCES pairs(id),
    FOREIGN KEY(creator_user_id) REFERENCES users(id),
    FOREIGN KEY(recipient_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS reminder_occurrences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,
    pair_id INTEGER NOT NULL,
    creator_user_id INTEGER NOT NULL,
    recipient_user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    scheduled_at_utc TEXT NOT NULL,
    next_attempt_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    handled_action TEXT,
    telegram_message_id INTEGER,
    delivery_attempts_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    sent_at TEXT,
    delivered_at TEXT,
    acknowledged_at TEXT,
    cancelled_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(rule_id) REFERENCES reminder_rules(id),
    FOREIGN KEY(pair_id) REFERENCES pairs(id),
    FOREIGN KEY(creator_user_id) REFERENCES users(id),
    FOREIGN KEY(recipient_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS care_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_code TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    category_label TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    emoji TEXT NOT NULL,
    sender_role TEXT NOT NULL DEFAULT 'unspecified',
    recipient_role TEXT NOT NULL DEFAULT 'unspecified',
    is_active INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS care_dispatches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair_id INTEGER NOT NULL,
    sender_user_id INTEGER NOT NULL,
    recipient_user_id INTEGER NOT NULL,
    template_code TEXT NOT NULL,
    category TEXT NOT NULL,
    category_label TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    emoji TEXT NOT NULL,
    status TEXT NOT NULL,
    telegram_message_id INTEGER,
    response_code TEXT,
    response_title TEXT,
    response_body TEXT,
    response_emoji TEXT,
    response_clicked_at TEXT,
    next_attempt_at_utc TEXT,
    processing_started_at TEXT,
    delivery_attempts_count INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT,
    delivered_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(pair_id) REFERENCES pairs(id),
    FOREIGN KEY(sender_user_id) REFERENCES users(id),
    FOREIGN KEY(recipient_user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS ui_panels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    panel_key TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS ephemeral_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    delete_after_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    action TEXT NOT NULL,
    payload_json TEXT,
    request_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(actor_user_id) REFERENCES users(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_pair_invites_token_hash ON pair_invites(token_hash);
CREATE INDEX IF NOT EXISTS idx_pair_invites_inviter_status ON pair_invites(inviter_user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pair_invites_invitee_status ON pair_invites(invitee_user_id, status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pair_invites_pending_inviter ON pair_invites(inviter_user_id) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_pairs_status_created_at ON pairs(status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pairs_active_canonical ON pairs(user_a_id, user_b_id) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_reminder_rules_pair_status ON reminder_rules(pair_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_reminder_occurrences_due ON reminder_occurrences(status, next_attempt_at_utc, scheduled_at_utc);
CREATE INDEX IF NOT EXISTS idx_reminder_occurrences_pair ON reminder_occurrences(pair_id, scheduled_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_reminder_occurrences_rule ON reminder_occurrences(rule_id, scheduled_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_care_templates_category ON care_templates(is_active, category, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_care_dispatches_pair_created ON care_dispatches(pair_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_care_dispatches_sender_created ON care_dispatches(sender_user_id, pair_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_care_dispatches_recipient_created ON care_dispatches(recipient_user_id, pair_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_care_dispatches_due ON care_dispatches(status, next_attempt_at_utc, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ui_panels_user_key ON ui_panels(user_id, panel_key);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ephemeral_chat_message ON ephemeral_messages(chat_id, message_id);
CREATE INDEX IF NOT EXISTS idx_ephemeral_due ON ephemeral_messages(status, delete_after_utc);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_created ON audit_logs(actor_user_id, created_at DESC, id DESC);

CREATE TRIGGER IF NOT EXISTS trg_pairs_prevent_multi_active_insert
BEFORE INSERT ON pairs
WHEN NEW.status = 'active'
BEGIN
    SELECT CASE
        WHEN EXISTS (
            SELECT 1
            FROM pairs p
            WHERE p.status = 'active'
              AND (
                p.user_a_id = NEW.user_a_id OR
                p.user_b_id = NEW.user_a_id OR
                p.user_a_id = NEW.user_b_id OR
                p.user_b_id = NEW.user_b_id
              )
        )
        THEN RAISE(ABORT, 'active pair already exists for one of the users')
    END;
END;

CREATE TRIGGER IF NOT EXISTS trg_pairs_prevent_multi_active_update
BEFORE UPDATE OF status, user_a_id, user_b_id ON pairs
WHEN NEW.status = 'active'
BEGIN
    SELECT CASE
        WHEN EXISTS (
            SELECT 1
            FROM pairs p
            WHERE p.id != OLD.id
              AND p.status = 'active'
              AND (
                p.user_a_id = NEW.user_a_id OR
                p.user_b_id = NEW.user_a_id OR
                p.user_a_id = NEW.user_b_id OR
                p.user_b_id = NEW.user_b_id
              )
        )
        THEN RAISE(ABORT, 'active pair already exists for one of the users')
    END;
END;
"""

_CARE_DISPATCHES_LEGACY_COLUMNS: dict[str, str] = {
    'next_attempt_at_utc': 'TEXT',
    'processing_started_at': 'TEXT',
    'delivery_attempts_count': 'INTEGER NOT NULL DEFAULT 0',
}

_USERS_COMPAT_COLUMNS: dict[str, str] = {
    'relationship_role': "TEXT NOT NULL DEFAULT 'unspecified'",
}

_CARE_TEMPLATE_COMPAT_COLUMNS: dict[str, str] = {
    'sender_role': "TEXT NOT NULL DEFAULT 'unspecified'",
    'recipient_role': "TEXT NOT NULL DEFAULT 'unspecified'",
}


_REMINDER_RULES_COMPAT_COLUMNS: dict[str, str] = {
    'recurrence_every': 'INTEGER NOT NULL DEFAULT 1',
    'recurrence_unit': 'TEXT',
}

_AUDIT_LOGS_SQL = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_user_id INTEGER,
    entity_type TEXT NOT NULL,
    entity_id TEXT,
    action TEXT NOT NULL,
    payload_json TEXT,
    request_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(actor_user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created ON audit_logs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor_created ON audit_logs(actor_user_id, created_at DESC, id DESC);
"""


@dataclass(slots=True, frozen=True)
class DatabaseCheckpointResult:
    mode: str
    busy: int
    log_frames: int
    checkpointed_frames: int


class SQLiteTransaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    async def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return await asyncio.to_thread(self._connection.execute, query, parameters)

    async def executescript(self, script: str) -> None:
        await asyncio.to_thread(self._connection.executescript, script)

    async def fetchone(self, query: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        cursor = await self.execute(query, parameters)
        try:
            return await asyncio.to_thread(cursor.fetchone)
        finally:
            cursor.close()

    async def fetchall(self, query: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        cursor = await self.execute(query, parameters)
        try:
            return list(await asyncio.to_thread(cursor.fetchall))
        finally:
            cursor.close()


class SQLiteDatabase:
    def __init__(
        self,
        path: Path,
        *,
        synchronous: str = 'FULL',
        journal_mode: str = 'WAL',
        busy_timeout_ms: int = 15000,
        wal_autocheckpoint_pages: int = 1000,
        journal_size_limit_bytes: int = 67108864,
    ) -> None:
        self._path = path
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self._synchronous = synchronous.upper()
        self._journal_mode = journal_mode.upper()
        self._busy_timeout_ms = max(1000, busy_timeout_ms)
        self._wal_autocheckpoint_pages = max(100, wal_autocheckpoint_pages)
        self._journal_size_limit_bytes = max(1024 * 1024, journal_size_limit_bytes)

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._connection is not None:
            return
        self._connection = sqlite3.connect(
            self._path,
            check_same_thread=False,
            isolation_level=None,
            timeout=self._busy_timeout_ms / 1000,
        )
        self._connection.row_factory = sqlite3.Row
        await asyncio.to_thread(self._apply_connection_pragmas)
        await asyncio.to_thread(self._connection.executescript, SCHEMA_SQL)
        await asyncio.to_thread(self._apply_compat_migrations)

    async def close(self) -> None:
        if self._connection is None:
            return
        connection = self._connection
        self._connection = None
        await asyncio.to_thread(connection.close)

    @property
    def path(self) -> Path:
        return self._path

    @property
    def journal_mode(self) -> str:
        return self._journal_mode

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError('SQLite connection is not initialized.')
        return self._connection

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SQLiteTransaction]:
        async with self._lock:
            connection = self.connection
            await asyncio.to_thread(connection.execute, 'BEGIN IMMEDIATE')
            transaction = SQLiteTransaction(connection)
            try:
                yield transaction
            except Exception:
                await asyncio.to_thread(connection.rollback)
                raise
            else:
                await asyncio.to_thread(connection.commit)

    async def fetchone(self, query: str, parameters: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        async with self._lock:
            cursor = await asyncio.to_thread(self.connection.execute, query, parameters)
            try:
                return await asyncio.to_thread(cursor.fetchone)
            finally:
                cursor.close()

    async def fetchall(self, query: str, parameters: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        async with self._lock:
            cursor = await asyncio.to_thread(self.connection.execute, query, parameters)
            try:
                return list(await asyncio.to_thread(cursor.fetchall))
            finally:
                cursor.close()

    async def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> None:
        async with self.transaction() as tx:
            await tx.execute(query, parameters)

    async def ping(self) -> bool:
        row = await self.fetchone('SELECT 1 AS ok')
        return row is not None and int(row['ok']) == 1

    async def run_quick_check(self, *, max_errors: int = 1) -> list[str]:
        async with self._lock:
            cursor = await asyncio.to_thread(self.connection.execute, f'PRAGMA quick_check({int(max_errors)})')
            try:
                rows = list(await asyncio.to_thread(cursor.fetchall))
            finally:
                cursor.close()
        messages = [str(row[0]) for row in rows if row and row[0] is not None]
        if messages == ['ok']:
            return []
        return messages

    async def run_foreign_key_check(self) -> list[str]:
        async with self._lock:
            cursor = await asyncio.to_thread(self.connection.execute, 'PRAGMA foreign_key_check')
            try:
                rows = list(await asyncio.to_thread(cursor.fetchall))
            finally:
                cursor.close()
        return [f"table={row[0]} rowid={row[1]} parent={row[2]} fk={row[3]}" for row in rows]

    async def checkpoint(self, mode: str = 'PASSIVE') -> DatabaseCheckpointResult:
        if self._journal_mode != 'WAL':
            checkpoint_mode = mode.upper()
            return DatabaseCheckpointResult(mode=checkpoint_mode, busy=0, log_frames=0, checkpointed_frames=0)
        checkpoint_mode = mode.upper()
        if checkpoint_mode not in {'PASSIVE', 'FULL', 'RESTART', 'TRUNCATE'}:
            raise ValueError('Unsupported checkpoint mode.')
        async with self._lock:
            cursor = await asyncio.to_thread(self.connection.execute, f'PRAGMA wal_checkpoint({checkpoint_mode})')
            try:
                row = await asyncio.to_thread(cursor.fetchone)
            finally:
                cursor.close()
        if row is None:
            return DatabaseCheckpointResult(mode=checkpoint_mode, busy=0, log_frames=0, checkpointed_frames=0)
        return DatabaseCheckpointResult(
            mode=checkpoint_mode,
            busy=int(row[0]),
            log_frames=int(row[1]),
            checkpointed_frames=int(row[2]),
        )

    async def backup_to(self, target_path: Path) -> Path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(target_path.suffix + '.tmp')
        try:
            async with self._lock:
                await asyncio.to_thread(self._backup_sync, tmp_path)
            tmp_path.replace(target_path)
            return target_path
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    async def optimize(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self.connection.execute, 'PRAGMA optimize')

    def _apply_connection_pragmas(self) -> None:
        connection = self.connection
        connection.execute('PRAGMA foreign_keys = ON')
        connection.execute(f'PRAGMA journal_mode = {self._journal_mode}')
        connection.execute(f'PRAGMA synchronous = {self._synchronous}')
        connection.execute(f'PRAGMA busy_timeout = {self._busy_timeout_ms}')
        if self._journal_mode == 'WAL':
            connection.execute(f'PRAGMA wal_autocheckpoint = {self._wal_autocheckpoint_pages}')
        connection.execute(f'PRAGMA journal_size_limit = {self._journal_size_limit_bytes}')
        connection.execute('PRAGMA temp_store = MEMORY')
        try:
            connection.execute('PRAGMA trusted_schema = OFF')
        except sqlite3.DatabaseError:
            pass

    def _backup_sync(self, target_path: Path) -> None:
        target = sqlite3.connect(target_path)
        try:
            target.row_factory = sqlite3.Row
            self.connection.backup(target)
            target.commit()
        finally:
            target.close()

    def _apply_compat_migrations(self) -> None:
        connection = self.connection
        existing_tables = {row['name'] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'users' in existing_tables:
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(users)')}
            for column_name, definition in _USERS_COMPAT_COLUMNS.items():
                if column_name not in columns:
                    connection.execute(f'ALTER TABLE users ADD COLUMN {column_name} {definition}')
        if 'care_templates' in existing_tables:
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(care_templates)')}
            for column_name, definition in _CARE_TEMPLATE_COMPAT_COLUMNS.items():
                if column_name not in columns:
                    connection.execute(f'ALTER TABLE care_templates ADD COLUMN {column_name} {definition}')
        if 'care_dispatches' in existing_tables:
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(care_dispatches)')}
            for column_name, definition in _CARE_DISPATCHES_LEGACY_COLUMNS.items():
                if column_name not in columns:
                    connection.execute(f'ALTER TABLE care_dispatches ADD COLUMN {column_name} {definition}')
            connection.execute('UPDATE care_dispatches SET next_attempt_at_utc = COALESCE(next_attempt_at_utc, created_at)')
            connection.execute('UPDATE care_dispatches SET delivery_attempts_count = COALESCE(delivery_attempts_count, 0)')
        if 'reminder_rules' in existing_tables:
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(reminder_rules)')}
            for column_name, definition in _REMINDER_RULES_COMPAT_COLUMNS.items():
                if column_name not in columns:
                    connection.execute(f'ALTER TABLE reminder_rules ADD COLUMN {column_name} {definition}')
            connection.execute('UPDATE reminder_rules SET recurrence_every = COALESCE(recurrence_every, 1)')
        connection.executescript(_AUDIT_LOGS_SQL)
        connection.commit()

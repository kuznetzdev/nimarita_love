from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from nimarita.config import Settings
from nimarita.infra.sqlite import DatabaseCheckpointResult, SQLiteDatabase
from nimarita.repositories.care import CareRepository
from nimarita.repositories.pairing import PairingRepository
from nimarita.repositories.reminders import ReminderRepository
from nimarita.services.audit import AuditService

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class WorkerHeartbeat:
    name: str
    running: bool = False
    last_beat_at: datetime | None = None
    last_error: str | None = None
    iterations: int = 0


class HeartbeatRegistry:
    def __init__(self) -> None:
        self._items: dict[str, WorkerHeartbeat] = {}

    def start(self, name: str) -> None:
        item = self._items.setdefault(name, WorkerHeartbeat(name=name))
        item.running = True
        item.last_error = None
        item.last_beat_at = datetime.now(tz=UTC)

    def beat(self, name: str) -> None:
        item = self._items.setdefault(name, WorkerHeartbeat(name=name))
        item.running = True
        item.iterations += 1
        item.last_beat_at = datetime.now(tz=UTC)

    def error(self, name: str, error_text: str) -> None:
        item = self._items.setdefault(name, WorkerHeartbeat(name=name))
        item.running = True
        item.last_error = error_text[:500]
        item.last_beat_at = datetime.now(tz=UTC)

    def stop(self, name: str) -> None:
        item = self._items.setdefault(name, WorkerHeartbeat(name=name))
        item.running = False
        item.last_beat_at = datetime.now(tz=UTC)

    def snapshot(self) -> list[WorkerHeartbeat]:
        return [
            WorkerHeartbeat(
                name=item.name,
                running=item.running,
                last_beat_at=item.last_beat_at,
                last_error=item.last_error,
                iterations=item.iterations,
            )
            for item in sorted(self._items.values(), key=lambda value: value.name)
        ]


@dataclass(slots=True, frozen=True)
class StartupRecoveryResult:
    expired_invites: int
    recovered_reminders: int
    recovered_care: int


@dataclass(slots=True, frozen=True)
class DatabaseAuditSnapshot:
    checked_at: datetime
    ok: bool
    quick_check_errors: tuple[str, ...]
    foreign_key_errors: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class BackupSnapshot:
    created_at: datetime
    ok: bool
    path: str | None
    size_bytes: int = 0
    error: str | None = None


@dataclass(slots=True, frozen=True)
class DatabaseMaintenanceSnapshot:
    audited: DatabaseAuditSnapshot | None
    checkpoint: DatabaseCheckpointResult | None
    backup: BackupSnapshot | None


class SystemService:
    def __init__(
        self,
        *,
        settings: Settings,
        database: SQLiteDatabase,
        pairing: PairingRepository,
        reminders: ReminderRepository,
        care: CareRepository,
        heartbeats: HeartbeatRegistry,
        audit: AuditService,
    ) -> None:
        self._settings = settings
        self._database = database
        self._pairing = pairing
        self._reminders = reminders
        self._care = care
        self._heartbeats = heartbeats
        self._audit = audit
        self._started_at = datetime.now(tz=UTC)
        self._last_database_audit: DatabaseAuditSnapshot | None = None
        self._last_checkpoint: DatabaseCheckpointResult | None = None
        self._last_backup: BackupSnapshot | None = None
        self._deployment_warnings = self._build_deployment_warnings()

    @property
    def started_at(self) -> datetime:
        return self._started_at

    async def log_deployment_warnings(self) -> None:
        if not self._deployment_warnings:
            return
        for warning in self._deployment_warnings:
            logger.warning('Deployment warning: %s', warning)
        await self._audit.record(
            action='sqlite_deployment_warnings',
            entity_type='system',
            entity_id='database',
            payload={'warnings': list(self._deployment_warnings)},
        )

    async def reconcile_startup(self) -> StartupRecoveryResult:
        now = datetime.now(tz=UTC)
        expired_invites = await self._pairing.expire_due_invites(now)
        recovered_reminders = await self._reminders.requeue_stale_processing(
            now=now,
            stale_before=now - timedelta(seconds=self._settings.processing_stale_seconds),
            max_retries=self._settings.reminder_max_retries,
            retry_base_seconds=self._settings.reminder_retry_base_seconds,
        )
        recovered_care = await self._care.requeue_stale_processing(
            now=now,
            stale_before=now - timedelta(seconds=self._settings.processing_stale_seconds),
            max_retries=self._settings.care_max_retries,
            retry_base_seconds=self._settings.care_retry_base_seconds,
        )
        if expired_invites or recovered_reminders or recovered_care:
            logger.info(
                'Startup reconciliation completed expired_invites=%s recovered_reminders=%s recovered_care=%s',
                expired_invites,
                recovered_reminders,
                recovered_care,
            )
        await self._audit.record(
            action='startup_reconciliation',
            entity_type='system',
            entity_id='runtime',
            payload={
                'expired_invites': expired_invites,
                'recovered_reminders': recovered_reminders,
                'recovered_care': recovered_care,
            },
        )
        return StartupRecoveryResult(
            expired_invites=expired_invites,
            recovered_reminders=recovered_reminders,
            recovered_care=recovered_care,
        )

    async def run_startup_database_audit(self) -> DatabaseAuditSnapshot:
        if not self._settings.sqlite_quick_check_on_startup:
            snapshot = DatabaseAuditSnapshot(
                checked_at=datetime.now(tz=UTC),
                ok=True,
                quick_check_errors=(),
                foreign_key_errors=(),
            )
            self._last_database_audit = snapshot
            return snapshot
        snapshot = await self.audit_database(reason='startup')
        if not snapshot.ok and self._settings.sqlite_fail_fast_on_integrity_error:
            raise RuntimeError('SQLite startup audit failed.')
        return snapshot

    async def audit_database(self, *, reason: str) -> DatabaseAuditSnapshot:
        quick_errors = tuple(await self._database.run_quick_check(max_errors=5))
        fk_errors = tuple(await self._database.run_foreign_key_check())
        checked_at = datetime.now(tz=UTC)
        snapshot = DatabaseAuditSnapshot(
            checked_at=checked_at,
            ok=not quick_errors and not fk_errors,
            quick_check_errors=quick_errors,
            foreign_key_errors=fk_errors,
        )
        self._last_database_audit = snapshot
        if snapshot.ok:
            logger.info('SQLite audit passed reason=%s', reason)
        else:
            logger.error(
                'SQLite audit failed reason=%s quick_errors=%s foreign_key_errors=%s',
                reason,
                list(quick_errors),
                list(fk_errors),
            )
        await self._audit.record(
            action='sqlite_audit',
            entity_type='system',
            entity_id='database',
            payload={
                'reason': reason,
                'ok': snapshot.ok,
                'quick_check_errors': list(quick_errors),
                'foreign_key_errors': list(fk_errors),
            },
        )
        return snapshot

    async def checkpoint_database(self, *, mode: str | None = None, reason: str) -> DatabaseCheckpointResult:
        result = await self._database.checkpoint(mode=mode or self._settings.sqlite_checkpoint_mode)
        self._last_checkpoint = result
        logger.info(
            'SQLite checkpoint completed reason=%s mode=%s busy=%s log_frames=%s checkpointed_frames=%s',
            reason,
            result.mode,
            result.busy,
            result.log_frames,
            result.checkpointed_frames,
        )
        await self._audit.record(
            action='sqlite_checkpoint',
            entity_type='system',
            entity_id='database',
            payload={
                'reason': reason,
                'mode': result.mode,
                'busy': result.busy,
                'log_frames': result.log_frames,
                'checkpointed_frames': result.checkpointed_frames,
            },
        )
        return result

    async def create_backup(self, *, reason: str) -> BackupSnapshot:
        if not self._settings.backup_enabled:
            snapshot = BackupSnapshot(created_at=datetime.now(tz=UTC), ok=False, path=None, error='backup disabled')
            self._last_backup = snapshot
            return snapshot

        now = datetime.now(tz=UTC)
        backup_dir = self._settings.backup_directory
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_name = f"nimarita_{now.strftime('%Y%m%d_%H%M%S')}.sqlite3"
        backup_path = backup_dir / backup_name
        try:
            await self._database.backup_to(backup_path)
            size_bytes = backup_path.stat().st_size if backup_path.exists() else 0
            snapshot = BackupSnapshot(
                created_at=now,
                ok=True,
                path=str(backup_path),
                size_bytes=size_bytes,
            )
            self._last_backup = snapshot
            await self._rotate_backups()
            logger.info('SQLite backup completed reason=%s path=%s size_bytes=%s', reason, backup_path, size_bytes)
            await self._audit.record(
                action='sqlite_backup_created',
                entity_type='system',
                entity_id=str(backup_path.name),
                payload={'reason': reason, 'path': str(backup_path), 'size_bytes': size_bytes},
            )
            return snapshot
        except Exception as error:
            snapshot = BackupSnapshot(
                created_at=now,
                ok=False,
                path=str(backup_path),
                error=str(error) or error.__class__.__name__,
            )
            self._last_backup = snapshot
            logger.exception('SQLite backup failed reason=%s path=%s', reason, backup_path)
            await self._audit.record(
                action='sqlite_backup_failed',
                entity_type='system',
                entity_id=str(backup_path.name),
                payload={'reason': reason, 'path': str(backup_path), 'error': snapshot.error},
            )
            return snapshot

    async def _rotate_backups(self) -> None:
        retention = max(1, self._settings.backup_retention)
        backup_dir = self._settings.backup_directory
        backups = sorted(
            (item for item in backup_dir.glob('nimarita_*.sqlite3') if item.is_file()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for stale in backups[retention:]:
            try:
                stale.unlink(missing_ok=True)
            except Exception:
                logger.exception('Failed to remove stale backup %s', stale)

    async def build_readiness_payload(self) -> dict[str, Any]:
        db_ok = await self._database.ping()
        snapshots = self._heartbeats.snapshot()
        stale_after = timedelta(seconds=self._settings.worker_heartbeat_stale_seconds)
        now = datetime.now(tz=UTC)
        workers: list[dict[str, Any]] = []
        workers_ok = True
        for item in snapshots:
            fresh = item.last_beat_at is not None and now - item.last_beat_at <= stale_after
            item_ok = item.running and fresh
            workers_ok = workers_ok and item_ok
            workers.append(
                {
                    'name': item.name,
                    'running': item.running,
                    'last_beat_at': item.last_beat_at.isoformat() if item.last_beat_at else None,
                    'iterations': item.iterations,
                    'last_error': item.last_error,
                    'fresh': fresh,
                    'ok': item_ok,
                }
            )
        audit_ok = self._last_database_audit.ok if self._last_database_audit is not None else True
        last_backup_age_seconds = None
        if self._last_backup is not None:
            last_backup_age_seconds = int((now - self._last_backup.created_at).total_seconds())
        return {
            'ok': db_ok and workers_ok and audit_ok,
            'service': 'nimarita',
            'started_at': self._started_at.isoformat(),
            'uptime_seconds': int((now - self._started_at).total_seconds()),
            'checks': {
                'db': {
                    'ok': db_ok,
                    'audit_ok': audit_ok,
                    'last_audit': self._serialize_database_audit(self._last_database_audit),
                    'last_checkpoint': self._serialize_checkpoint(self._last_checkpoint),
                    'last_backup': self._serialize_backup(self._last_backup),
                    'last_backup_age_seconds': last_backup_age_seconds,
                },
                'workers': workers,
                'deployment': {
                    'database_path': str(self._settings.database_path),
                    'backup_directory': str(self._settings.backup_directory),
                    'sqlite_journal_mode': self._settings.sqlite_journal_mode,
                    'sqlite_synchronous': self._settings.sqlite_synchronous,
                    'warnings': list(self._deployment_warnings),
                },
            },
        }

    def _build_deployment_warnings(self) -> tuple[str, ...]:
        warnings: list[str] = [
            'SQLite deployment assumes a single writer; do not run multiple Railway replicas against the same database file.',
        ]
        railway_volume_raw = os.getenv('RAILWAY_VOLUME_MOUNT_PATH')
        if railway_volume_raw:
            railway_volume = Path(railway_volume_raw)
            if not _is_path_inside(railway_volume, self._settings.database_path):
                warnings.append('SQLite database path is outside the Railway volume; persistence across restarts is not guaranteed.')
            if self._settings.backup_enabled and not _is_path_inside(railway_volume, self._settings.backup_directory):
                warnings.append('Backup directory is outside the Railway volume; backup files will be ephemeral.')
            if self._settings.sqlite_journal_mode == 'WAL':
                warnings.append('WAL mode on Railway requires persisting -wal and -shm sidecar files and should remain single-instance.')
        if self._settings.sqlite_journal_mode == 'OFF':
            warnings.append('SQLite journal_mode=OFF disables crash recovery and is unsafe for production.')
        if self._settings.sqlite_synchronous == 'OFF':
            warnings.append('SQLite synchronous=OFF increases corruption risk after crashes.')
        if self._settings.session_secret == self._settings.bot_token:
            warnings.append('APP_SESSION_SECRET matches BOT_TOKEN; rotate to a dedicated web-session secret.')
        return tuple(warnings)

    def maintenance_snapshot(self) -> DatabaseMaintenanceSnapshot:
        return DatabaseMaintenanceSnapshot(
            audited=self._last_database_audit,
            checkpoint=self._last_checkpoint,
            backup=self._last_backup,
        )

    async def graceful_shutdown(self) -> None:
        await self.checkpoint_database(mode='TRUNCATE', reason='shutdown')
        if self._settings.backup_on_shutdown:
            await self.create_backup(reason='shutdown')

    @staticmethod
    def _serialize_database_audit(snapshot: DatabaseAuditSnapshot | None) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        return {
            'checked_at': snapshot.checked_at.isoformat(),
            'ok': snapshot.ok,
            'quick_check_errors': list(snapshot.quick_check_errors),
            'foreign_key_errors': list(snapshot.foreign_key_errors),
        }

    @staticmethod
    def _serialize_checkpoint(snapshot: DatabaseCheckpointResult | None) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        return {
            'mode': snapshot.mode,
            'busy': snapshot.busy,
            'log_frames': snapshot.log_frames,
            'checkpointed_frames': snapshot.checkpointed_frames,
        }

    @staticmethod
    def _serialize_backup(snapshot: BackupSnapshot | None) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        return {
            'created_at': snapshot.created_at.isoformat(),
            'ok': snapshot.ok,
            'path': snapshot.path,
            'size_bytes': snapshot.size_bytes,
            'error': snapshot.error,
        }


def _is_path_inside(root: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

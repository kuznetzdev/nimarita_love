from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from nimarita.config import Settings
from nimarita.services.system import HeartbeatRegistry, SystemService

logger = logging.getLogger(__name__)


class MaintenanceWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        system: SystemService,
        heartbeats: HeartbeatRegistry,
    ) -> None:
        self._settings = settings
        self._system = system
        self._heartbeats = heartbeats
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._name = 'maintenance-worker'
        now = datetime.now(tz=UTC)
        self._next_checkpoint_at = now + timedelta(seconds=max(1, settings.sqlite_checkpoint_interval_seconds))
        self._next_quick_check_at = now + timedelta(seconds=max(1, settings.sqlite_quick_check_interval_seconds))
        self._next_backup_at = now + timedelta(seconds=max(1, settings.backup_interval_seconds))

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name='nimarita-maintenance-worker')

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run(self) -> None:
        logger.info('Maintenance worker started')
        self._heartbeats.start(self._name)
        try:
            while not self._stop.is_set():
                try:
                    await self._process_once()
                    self._heartbeats.beat(self._name)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._heartbeats.error(self._name, str(error) or error.__class__.__name__)
                    logger.exception('Maintenance worker iteration failed')
                await asyncio.sleep(self._settings.maintenance_worker_poll_seconds)
        except asyncio.CancelledError:
            logger.info('Maintenance worker cancelled')
            raise
        finally:
            self._heartbeats.stop(self._name)
            logger.info('Maintenance worker stopped')

    async def _process_once(self) -> None:
        now = datetime.now(tz=UTC)
        if now >= self._next_checkpoint_at:
            await self._system.checkpoint_database(reason='scheduled')
            self._next_checkpoint_at = now + timedelta(seconds=max(1, self._settings.sqlite_checkpoint_interval_seconds))

        if self._settings.sqlite_quick_check_interval_seconds > 0 and now >= self._next_quick_check_at:
            await self._system.audit_database(reason='scheduled')
            self._next_quick_check_at = now + timedelta(seconds=max(1, self._settings.sqlite_quick_check_interval_seconds))

        if self._settings.backup_enabled and self._settings.backup_interval_seconds > 0 and now >= self._next_backup_at:
            await self._system.create_backup(reason='scheduled')
            self._next_backup_at = now + timedelta(seconds=max(1, self._settings.backup_interval_seconds))

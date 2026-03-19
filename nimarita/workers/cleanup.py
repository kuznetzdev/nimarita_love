from __future__ import annotations

import asyncio
import logging

from nimarita.config import Settings
from nimarita.services.system import HeartbeatRegistry
from nimarita.telegram.ui import TelegramUI

logger = logging.getLogger(__name__)


class CleanupWorker:
    def __init__(self, *, settings: Settings, ui: TelegramUI, heartbeats: HeartbeatRegistry) -> None:
        self._settings = settings
        self._ui = ui
        self._heartbeats = heartbeats
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._name = 'cleanup-worker'

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name='nimarita-cleanup-worker')

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
        logger.info('Cleanup worker started')
        self._heartbeats.start(self._name)
        try:
            while not self._stop.is_set():
                try:
                    await self._ui.cleanup_due_deletes(limit=self._settings.cleanup_batch_size)
                    self._heartbeats.beat(self._name)
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    self._heartbeats.error(self._name, str(error) or error.__class__.__name__)
                    logger.exception('Cleanup worker iteration failed')
                await asyncio.sleep(self._settings.cleanup_worker_poll_seconds)
        except asyncio.CancelledError:
            logger.info('Cleanup worker cancelled')
            raise
        finally:
            self._heartbeats.stop(self._name)
            logger.info('Cleanup worker stopped')

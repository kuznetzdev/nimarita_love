from __future__ import annotations

import asyncio
import logging

from nimarita.config import Settings
from nimarita.services.reminders import ReminderDeliveryFailure, ReminderService
from nimarita.services.system import HeartbeatRegistry
from nimarita.telegram.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class ReminderWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        reminders: ReminderService,
        notifier: TelegramNotifier,
        heartbeats: HeartbeatRegistry,
    ) -> None:
        self._settings = settings
        self._reminders = reminders
        self._notifier = notifier
        self._heartbeats = heartbeats
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._name = 'reminder-worker'

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name='nimarita-reminder-worker')

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
        logger.info('Reminder worker started')
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
                    logger.exception('Reminder worker iteration failed')
                await asyncio.sleep(self._settings.reminder_worker_poll_seconds)
        except asyncio.CancelledError:
            logger.info('Reminder worker cancelled')
            raise
        finally:
            self._heartbeats.stop(self._name)
            logger.info('Reminder worker stopped')

    async def _process_once(self) -> None:
        batch = await self._reminders.claim_due_occurrences(limit=self._settings.reminder_batch_size)
        if not batch:
            return
        semaphore = asyncio.Semaphore(max(1, self._settings.reminder_worker_concurrency))
        await asyncio.gather(*(self._deliver_with_limit(item, semaphore) for item in batch))

    async def _deliver_with_limit(self, envelope, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            await self._deliver_one(envelope)

    async def _deliver_one(self, envelope) -> None:
        try:
            message_id = await self._notifier.send_reminder(envelope, app_link=self._settings.direct_main_app_link)
        except Exception as error:
            logger.exception('Failed to deliver reminder occurrence_id=%s', envelope.occurrence.id)
            failure = await self._reminders.mark_delivery_failure(
                occurrence_id=envelope.occurrence.id,
                error_text=str(error) or error.__class__.__name__,
            )
            await self._notify_failure_if_needed(failure, envelope.occurrence.text)
            return

        delivered = await self._reminders.mark_delivered(
            occurrence_id=envelope.occurrence.id,
            telegram_message_id=message_id,
        )
        await self._notifier.notify_reminder_delivered(delivered)

    async def _notify_failure_if_needed(self, failure: ReminderDeliveryFailure, text: str) -> None:
        if not failure.final_failure:
            return
        await self._notifier.notify_reminder_failed(
            creator=failure.creator,
            recipient=failure.recipient,
            text=text,
            error_text=failure.occurrence.last_error or 'unknown error',
        )

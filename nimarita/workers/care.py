from __future__ import annotations

import asyncio
import logging

from nimarita.config import Settings
from nimarita.services.care import CareDeliveryFailure, CareService
from nimarita.services.system import HeartbeatRegistry
from nimarita.telegram.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class CareWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        care: CareService,
        notifier: TelegramNotifier,
        heartbeats: HeartbeatRegistry,
    ) -> None:
        self._settings = settings
        self._care = care
        self._notifier = notifier
        self._heartbeats = heartbeats
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._name = 'care-worker'

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name='nimarita-care-worker')

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
        logger.info('Care worker started')
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
                    logger.exception('Care worker iteration failed')
                await asyncio.sleep(self._settings.care_worker_poll_seconds)
        except asyncio.CancelledError:
            logger.info('Care worker cancelled')
            raise
        finally:
            self._heartbeats.stop(self._name)
            logger.info('Care worker stopped')

    async def _process_once(self) -> None:
        batch = await self._care.claim_due_dispatches(limit=self._settings.care_batch_size)
        if not batch:
            return
        semaphore = asyncio.Semaphore(max(1, self._settings.care_worker_concurrency))
        await asyncio.gather(*(self._deliver_with_limit(item, semaphore) for item in batch))

    async def _deliver_with_limit(self, envelope, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            await self._deliver_one(envelope)

    async def _deliver_one(self, envelope) -> None:
        try:
            message_id = await self._notifier.send_care(envelope, app_link=self._settings.direct_main_app_link)
        except Exception as error:
            logger.exception('Failed to deliver care dispatch_id=%s', envelope.dispatch.id)
            failure = await self._care.mark_delivery_failure(
                dispatch_id=envelope.dispatch.id,
                error_text=str(error) or error.__class__.__name__,
            )
            await self._notify_failure_if_needed(failure)
            return

        await self._care.mark_sent(
            dispatch_id=envelope.dispatch.id,
            telegram_message_id=message_id,
        )

    async def _notify_failure_if_needed(self, failure: CareDeliveryFailure) -> None:
        if not failure.final_failure:
            return
        await self._notifier.notify_care_failed(
            sender=failure.sender,
            recipient=failure.recipient,
            template_title=failure.dispatch.title,
            error_text=failure.dispatch.last_error or 'неизвестная ошибка',
        )

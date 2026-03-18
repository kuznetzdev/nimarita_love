from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from config import (
    DELIVERY_RETRY_ATTEMPTS,
    DELIVERY_RETRY_DELAY_SECONDS,
    ROMANTIC_OPENERS,
    SCHEDULER_MISFIRE_GRACE_SECONDS,
)

from .models import ReminderRecord
from .storage import ReminderStorage

logger = logging.getLogger(__name__)


class ReminderScheduler:
    def __init__(self, bot: Bot, storage: ReminderStorage, timezone: ZoneInfo) -> None:
        self._bot = bot
        self._storage = storage
        self._timezone = timezone
        self._scheduler = AsyncIOScheduler(timezone=timezone)
        self._running = False

    async def start(self) -> None:
        if not self._running:
            self._scheduler.start()
            self._running = True
        await self.restore_active_reminders()

    async def shutdown(self) -> None:
        if self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False

    async def restore_active_reminders(self) -> None:
        reminders = await self._storage.list_all_active()
        for reminder in reminders:
            await self.schedule(reminder)
        logger.info("Restored %s active reminders.", len(reminders))

    async def schedule(self, reminder: ReminderRecord) -> None:
        self.unschedule(reminder.reminder_id)
        reminder_zone = self._resolve_timezone(reminder.timezone)

        if reminder.kind == "once":
            if reminder.run_at is None:
                logger.error("Reminder %s has kind=once but run_at is empty.", reminder.reminder_id)
                return

            run_at = reminder.run_at.astimezone(reminder_zone)
            if run_at <= datetime.now(tz=reminder_zone):
                await self._storage.deactivate_by_id(reminder.reminder_id)
                logger.info("Reminder %s expired and was deactivated.", reminder.reminder_id)
                return

            self._scheduler.add_job(
                self._deliver_once,
                trigger=DateTrigger(run_date=run_at),
                kwargs={"reminder_id": reminder.reminder_id},
                id=self._job_id(reminder.reminder_id),
                replace_existing=True,
                misfire_grace_time=SCHEDULER_MISFIRE_GRACE_SECONDS,
            )
            return

        if reminder.kind == "daily":
            if reminder.daily_time is None:
                logger.error(
                    "Reminder %s has kind=daily but daily_time is empty.",
                    reminder.reminder_id,
                )
                return

            hour, minute = self._parse_daily_time(reminder.reminder_id, reminder.daily_time)
            if hour is None or minute is None:
                return

            self._scheduler.add_job(
                self._deliver_daily,
                trigger=CronTrigger(
                    hour=hour,
                    minute=minute,
                    timezone=reminder_zone,
                ),
                kwargs={"reminder_id": reminder.reminder_id},
                id=self._job_id(reminder.reminder_id),
                replace_existing=True,
                misfire_grace_time=SCHEDULER_MISFIRE_GRACE_SECONDS,
            )
            return

        logger.error("Reminder %s has unsupported kind '%s'.", reminder.reminder_id, reminder.kind)

    def unschedule(self, reminder_id: int) -> None:
        job = self._scheduler.get_job(self._job_id(reminder_id))
        if job is not None:
            job.remove()

    async def _deliver_once(self, reminder_id: int) -> None:
        reminder = await self._storage.get_active_by_id(reminder_id)
        if reminder is None:
            self.unschedule(reminder_id)
            return

        delivered = await self._deliver(reminder)
        if delivered:
            await self._storage.deactivate_by_id(reminder_id)
            self.unschedule(reminder_id)
            return

        refreshed = await self._storage.get_active_by_id(reminder_id)
        if refreshed is not None:
            await self._schedule_retry(refreshed)

    async def _deliver_daily(self, reminder_id: int) -> None:
        reminder = await self._storage.get_active_by_id(reminder_id)
        if reminder is None:
            self.unschedule(reminder_id)
            return

        await self._deliver(reminder)

    async def _deliver(self, reminder: ReminderRecord) -> bool:
        for attempt in range(1, DELIVERY_RETRY_ATTEMPTS + 2):
            try:
                await self._send_reminder(reminder)
                return True
            except TelegramForbiddenError:
                logger.warning(
                    "User %s blocked bot, reminder %s deactivated.",
                    reminder.recipient_chat_id,
                    reminder.reminder_id,
                )
                await self._storage.deactivate_by_id(reminder.reminder_id)
                self.unschedule(reminder.reminder_id)
                return False
            except TelegramAPIError as error:
                if attempt > DELIVERY_RETRY_ATTEMPTS:
                    logger.error(
                        "Failed to deliver reminder %s after %s attempts: %s",
                        reminder.reminder_id,
                        attempt,
                        error,
                    )
                    return False
                logger.warning(
                    "Delivery attempt %s for reminder %s failed: %s",
                    attempt,
                    reminder.reminder_id,
                    error,
                )
                await asyncio.sleep(DELIVERY_RETRY_DELAY_SECONDS)
        return False

    async def _send_reminder(self, reminder: ReminderRecord) -> None:
        message_text = self._build_text(reminder)
        if reminder.voice and reminder.voice_file_id:
            await self._bot.send_voice(
                chat_id=reminder.recipient_chat_id,
                voice=reminder.voice_file_id,
                caption=message_text,
            )
            return

        if reminder.voice and not reminder.voice_file_id:
            logger.warning(
                "Reminder %s requested voice delivery without voice_file_id. Falling back to text.",
                reminder.reminder_id,
            )

        await self._bot.send_message(
            chat_id=reminder.recipient_chat_id,
            text=message_text,
        )

    async def _schedule_retry(self, reminder: ReminderRecord) -> None:
        retry_zone = self._resolve_timezone(reminder.timezone)
        retry_at = datetime.now(tz=retry_zone) + timedelta(seconds=DELIVERY_RETRY_DELAY_SECONDS)
        self._scheduler.add_job(
            self._deliver_once,
            trigger=DateTrigger(run_date=retry_at),
            kwargs={"reminder_id": reminder.reminder_id},
            id=self._job_id(reminder.reminder_id),
            replace_existing=True,
            misfire_grace_time=SCHEDULER_MISFIRE_GRACE_SECONDS,
        )

    @staticmethod
    def _job_id(reminder_id: int) -> str:
        return f"reminder:{reminder_id}"

    @staticmethod
    def _build_text(reminder: ReminderRecord) -> str:
        opener = random.choice(ROMANTIC_OPENERS)
        return f"{opener}\n\nНапоминание:\n«{reminder.text}»"

    @staticmethod
    def _parse_daily_time(reminder_id: int, daily_time: str) -> tuple[int | None, int | None]:
        try:
            hour, minute = map(int, daily_time.split(":"))
        except ValueError:
            logger.error(
                "Reminder %s has invalid daily_time '%s'.",
                reminder_id,
                daily_time,
            )
            return None, None
        return hour, minute

    def _resolve_timezone(self, timezone_name: str) -> ZoneInfo:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            logger.warning(
                "Unknown timezone '%s' for scheduler. Falling back to %s.",
                timezone_name,
                self._timezone.key,
            )
            return self._timezone

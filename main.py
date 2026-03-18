from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import create_router
from bot.scheduler import ReminderScheduler
from bot.storage import ReminderStorage
from bot.web_app import WebAppServer
from config import BOT_TOKEN, DB_PATH, MOSCOW_TZ, WEBAPP_ENABLED


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


async def run_bot() -> None:
    setup_logging()

    bot = Bot(token=BOT_TOKEN)
    dispatcher = Dispatcher(storage=MemoryStorage())

    storage = ReminderStorage(DB_PATH)
    await storage.init_schema()

    scheduler = ReminderScheduler(
        bot=bot,
        storage=storage,
        timezone=MOSCOW_TZ,
    )
    await scheduler.start()

    web_app: WebAppServer | None = None
    if WEBAPP_ENABLED:
        web_app = WebAppServer.from_config(storage=storage, scheduler=scheduler)
        await web_app.start()

    dispatcher.include_router(
        create_router(storage=storage, scheduler=scheduler, timezone=MOSCOW_TZ)
    )

    try:
        await dispatcher.start_polling(bot)
    finally:
        if web_app is not None:
            await web_app.stop()
        await scheduler.shutdown()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_bot())

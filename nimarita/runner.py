from __future__ import annotations

import asyncio

from nimarita.app import build_runtime
from nimarita.config import load_settings
from nimarita.logging import configure_logging


async def run() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    runtime = await build_runtime(settings)
    try:
        await runtime.start()
        await runtime.dispatcher.start_polling(runtime.bot)
    finally:
        await runtime.close()


def main() -> None:
    asyncio.run(run())

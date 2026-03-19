from __future__ import annotations

from aiogram import Bot
from aiogram.types import MenuButtonCommands, MenuButtonWebApp, WebAppInfo

from nimarita.config import Settings
from nimarita.domain.models import DashboardState


async def sync_private_menu_button(bot: Bot, *, chat_id: int, state: DashboardState, settings: Settings) -> None:
    if not settings.webapp_public_url:
        await bot.set_chat_menu_button(chat_id=chat_id, menu_button=MenuButtonCommands())
        return

    if state.mode == "active":
        text = "Открыть наше пространство 💖"
    elif state.mode == "incoming_invite":
        text = "Подтвердить пару 💌"
    else:
        text = "Создать пару 💞"

    await bot.set_chat_menu_button(
        chat_id=chat_id,
        menu_button=MenuButtonWebApp(
            text=text,
            web_app=WebAppInfo(url=settings.webapp_public_url),
        ),
    )

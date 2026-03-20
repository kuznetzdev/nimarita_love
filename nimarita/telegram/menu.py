from __future__ import annotations

from aiogram import Bot
from aiogram.types import MenuButtonCommands, MenuButtonWebApp, WebAppInfo

from nimarita.config import Settings
from nimarita.domain.models import DashboardState


def _build_menu_button(*, text: str, settings: Settings):
    if not settings.webapp_public_url:
        return MenuButtonCommands()
    return MenuButtonWebApp(
        text=text,
        web_app=WebAppInfo(url=settings.webapp_public_url),
    )


def _menu_text_for_state(state: DashboardState) -> str:
    if state.mode == 'active':
        return 'Открыть наше пространство 💖'
    if state.mode == 'incoming_invite':
        return 'Подтвердить пару 💌'
    return 'Создать пару 💞'


async def sync_default_menu_button(bot: Bot, *, settings: Settings) -> None:
    """Set a generic menu button for users before per-chat state sync happens."""
    await bot.set_chat_menu_button(
        menu_button=_build_menu_button(text='Открыть Nimarita 💞', settings=settings),
    )


async def sync_private_menu_button(bot: Bot, *, chat_id: int, state: DashboardState, settings: Settings) -> None:
    await bot.set_chat_menu_button(
        chat_id=chat_id,
        menu_button=_build_menu_button(text=_menu_text_for_state(state), settings=settings),
    )

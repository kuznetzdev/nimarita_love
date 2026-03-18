from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import WEBAPP_URL

from .models import ReminderRecord


def main_menu_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if WEBAPP_URL:
        builder.button(text="Mini App", web_app=WebAppInfo(url=WEBAPP_URL))
    builder.button(text="Разовое напоминание", callback_data="menu:add_once")
    builder.button(text="Ежедневное напоминание", callback_data="menu:add_daily")
    builder.button(text="Мои напоминания", callback_data="menu:list")
    builder.adjust(1)
    return builder.as_markup()


def cancel_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Отменить", callback_data="menu:cancel")
    builder.adjust(1)
    return builder.as_markup()


def reminders_keyboard(reminders: list[ReminderRecord]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for reminder in reminders:
        builder.button(
            text=f"Удалить #{reminder.reminder_id}",
            callback_data=f"rem:del:{reminder.reminder_id}",
        )
    builder.button(text="В меню", callback_data="menu:open")
    builder.adjust(1)
    return builder.as_markup()

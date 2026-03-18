from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import DATETIME_INPUT_FORMAT, TIME_INPUT_FORMAT, WEBAPP_URL

from .access import AccessDeniedError, AccessManager
from .keyboards import cancel_keyboard, main_menu_keyboard, reminders_keyboard
from .models import ReminderRecord
from .profiles import UserProfile
from .scheduler import ReminderScheduler
from .storage import ReminderStorage


class ReminderFlow(StatesGroup):
    once_text = State()
    once_datetime = State()
    daily_text = State()
    daily_time = State()


def create_router(storage: ReminderStorage, scheduler: ReminderScheduler, timezone: ZoneInfo) -> Router:
    router = Router()
    access = AccessManager()

    async def ensure_message_access(message: Message) -> UserProfile | None:
        user_id = message.from_user.id if message.from_user is not None else message.chat.id
        try:
            return access.ensure_allowed(user_id=user_id, chat_id=message.chat.id)
        except AccessDeniedError as error:
            await message.answer(str(error))
            return None

    async def ensure_callback_access(callback: CallbackQuery) -> UserProfile | None:
        if callback.message is None:
            return None
        try:
            return access.ensure_allowed(
                user_id=callback.from_user.id,
                chat_id=callback.message.chat.id,
            )
        except AccessDeniedError as error:
            await callback.message.answer(str(error))
            return None

    async def show_menu_message(message: Message, profile: UserProfile | None) -> None:
        await message.answer(_menu_text(profile), reply_markup=main_menu_keyboard())

    async def show_menu_callback(callback: CallbackQuery, profile: UserProfile | None) -> None:
        if callback.message is not None:
            await callback.message.answer(
                _menu_text(profile),
                reply_markup=main_menu_keyboard(),
            )

    @router.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext) -> None:
        profile = await ensure_message_access(message)
        await state.clear()
        if profile is None:
            return
        await message.answer(_start_text(profile))
        await show_menu_message(message, profile)

    @router.message(Command("menu"))
    async def cmd_menu(message: Message, state: FSMContext) -> None:
        profile = await ensure_message_access(message)
        await state.clear()
        if profile is None:
            return
        await show_menu_message(message, profile)

    @router.message(Command("cancel"))
    async def cmd_cancel(message: Message, state: FSMContext) -> None:
        profile = await ensure_message_access(message)
        await state.clear()
        if profile is None:
            return
        await message.answer("Текущий сценарий отменён.")
        await show_menu_message(message, profile)

    @router.callback_query(F.data == "menu:open")
    async def menu_open(callback: CallbackQuery, state: FSMContext) -> None:
        profile = await ensure_callback_access(callback)
        await callback.answer()
        await state.clear()
        if profile is None:
            return
        await show_menu_callback(callback, profile)

    @router.callback_query(F.data == "menu:cancel")
    async def menu_cancel(callback: CallbackQuery, state: FSMContext) -> None:
        profile = await ensure_callback_access(callback)
        await callback.answer()
        await state.clear()
        if profile is None:
            return
        await show_menu_callback(callback, profile)

    @router.callback_query(F.data == "menu:add_once")
    async def menu_add_once(callback: CallbackQuery, state: FSMContext) -> None:
        profile = await ensure_callback_access(callback)
        await callback.answer()
        await state.clear()
        if profile is None:
            return
        await state.set_state(ReminderFlow.once_text)
        if callback.message is not None:
            await callback.message.answer(
                "Напиши текст разового напоминания.",
                reply_markup=cancel_keyboard(),
            )

    @router.message(ReminderFlow.once_text)
    async def once_text_input(message: Message, state: FSMContext) -> None:
        if await ensure_message_access(message) is None:
            return
        if message.text is None:
            await message.answer("Пришли текст обычным сообщением.")
            return

        clean_text = message.text.strip()
        if not clean_text:
            await message.answer("Пустой текст не подойдёт. Напиши напоминание ещё раз.")
            return

        await state.update_data(text=clean_text)
        await state.set_state(ReminderFlow.once_datetime)
        await message.answer(
            "Теперь укажи дату и время в формате ДД.ММ.ГГГГ ЧЧ:ММ.\n"
            "Пример: 21.03.2026 19:30.",
            reply_markup=cancel_keyboard(),
        )

    @router.message(ReminderFlow.once_datetime)
    async def once_datetime_input(message: Message, state: FSMContext) -> None:
        profile = await ensure_message_access(message)
        if profile is None:
            await state.clear()
            return
        if message.text is None:
            await message.answer("Пришли дату и время текстом.")
            return

        parsed_datetime = _parse_moscow_datetime(message.text, timezone)
        if parsed_datetime is None:
            await message.answer(
                "Не смог разобрать дату. Используй формат ДД.ММ.ГГГГ ЧЧ:ММ, например 21.03.2026 19:30."
            )
            return

        now = datetime.now(tz=timezone)
        if parsed_datetime <= now:
            await message.answer("Это время уже прошло по Москве. Укажи будущую дату.")
            return

        state_data = await state.get_data()
        reminder_text = str(state_data.get("text", "")).strip()
        if not reminder_text:
            await state.clear()
            await message.answer("Состояние сбилось. Давай начнём заново.")
            await show_menu_message(message, profile)
            return

        reminder = await storage.create_once(
            chat_id=message.chat.id,
            text=reminder_text,
            run_at=parsed_datetime,
        )
        await scheduler.schedule(reminder)
        await state.clear()
        await message.answer(
            "Готово.\n"
            f"Напомню {parsed_datetime.strftime(DATETIME_INPUT_FORMAT)} по Москве."
        )
        await show_menu_message(message, profile)

    @router.callback_query(F.data == "menu:add_daily")
    async def menu_add_daily(callback: CallbackQuery, state: FSMContext) -> None:
        profile = await ensure_callback_access(callback)
        await callback.answer()
        await state.clear()
        if profile is None:
            return
        await state.set_state(ReminderFlow.daily_text)
        if callback.message is not None:
            await callback.message.answer(
                "Напиши текст ежедневного напоминания.",
                reply_markup=cancel_keyboard(),
            )

    @router.message(ReminderFlow.daily_text)
    async def daily_text_input(message: Message, state: FSMContext) -> None:
        if await ensure_message_access(message) is None:
            return
        if message.text is None:
            await message.answer("Пришли текст обычным сообщением.")
            return

        clean_text = message.text.strip()
        if not clean_text:
            await message.answer("Пустой текст не подойдёт. Напиши напоминание ещё раз.")
            return

        await state.update_data(text=clean_text)
        await state.set_state(ReminderFlow.daily_time)
        await message.answer(
            "Укажи время в формате ЧЧ:ММ.\n"
            "Пример: 08:45.",
            reply_markup=cancel_keyboard(),
        )

    @router.message(ReminderFlow.daily_time)
    async def daily_time_input(message: Message, state: FSMContext) -> None:
        profile = await ensure_message_access(message)
        if profile is None:
            await state.clear()
            return
        if message.text is None:
            await message.answer("Пришли время текстом.")
            return

        normalized_time = _parse_time(message.text)
        if normalized_time is None:
            await message.answer("Не распознал время. Используй формат ЧЧ:ММ, например 08:45.")
            return

        state_data = await state.get_data()
        reminder_text = str(state_data.get("text", "")).strip()
        if not reminder_text:
            await state.clear()
            await message.answer("Состояние сбилось. Давай начнём заново.")
            await show_menu_message(message, profile)
            return

        reminder = await storage.create_daily(
            chat_id=message.chat.id,
            text=reminder_text,
            daily_time=normalized_time,
            created_at=datetime.now(tz=timezone),
        )
        await scheduler.schedule(reminder)
        await state.clear()
        await message.answer(
            "Готово.\n"
            f"Буду напоминать каждый день в {normalized_time} по Москве."
        )
        await show_menu_message(message, profile)

    @router.callback_query(F.data == "menu:list")
    async def menu_list(callback: CallbackQuery) -> None:
        profile = await ensure_callback_access(callback)
        await callback.answer()
        if callback.message is None or profile is None:
            return

        reminders = await storage.list_active_by_chat(chat_id=callback.message.chat.id)
        if not reminders:
            await callback.message.answer("Активных напоминаний пока нет.")
            await show_menu_callback(callback, profile)
            return

        formatted = "\n\n".join(_format_reminder(item, timezone) for item in reminders)
        await callback.message.answer(
            f"Вот твои активные напоминания:\n\n{formatted}",
            reply_markup=reminders_keyboard(reminders),
        )

    @router.callback_query(F.data.startswith("rem:del:"))
    async def reminder_delete(callback: CallbackQuery) -> None:
        profile = await ensure_callback_access(callback)
        await callback.answer()
        if callback.message is None or profile is None:
            return

        reminder_id = _extract_reminder_id(callback.data or "")
        if reminder_id is None:
            await callback.message.answer("Не смог определить ID напоминания.")
            return

        removed = await storage.deactivate_for_chat(
            reminder_id=reminder_id,
            chat_id=callback.message.chat.id,
        )
        if not removed:
            await callback.message.answer(
                "Такое напоминание уже удалено или недоступно."
            )
            return

        scheduler.unschedule(reminder_id)
        await callback.message.answer(f"Напоминание #{reminder_id} удалено.")
        await show_menu_callback(callback, profile)

    @router.message()
    async def fallback_message(message: Message) -> None:
        if await ensure_message_access(message) is None:
            return
        await message.answer("Чтобы не ошибиться, выбери действие через /menu.")

    return router


def _menu_text(profile: UserProfile | None) -> str:
    prefix = f"Профиль: {profile.label}. " if profile is not None else ""
    if WEBAPP_URL:
        return prefix + "Выбирай действие: можно создать напоминание здесь или открыть Mini App."
    return prefix + "Выбирай действие: здесь можно создать и посмотреть напоминания."


def _start_text(profile: UserProfile | None) -> str:
    if profile is None:
        return (
            "Привет. Я твой бот-напоминалка.\n"
            "Все даты и время работают по Москве (МСК)."
        )
    return (
        f"Привет, {profile.label}. Я твой бот-напоминалка.\n"
        f"Профиль: {profile.role}, {profile.gender}.\n"
        "Все даты и время работают по Москве (МСК)."
    )


def _parse_moscow_datetime(raw: str, timezone: ZoneInfo) -> datetime | None:
    try:
        parsed = datetime.strptime(raw.strip(), DATETIME_INPUT_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone)


def _parse_time(raw: str) -> str | None:
    try:
        parsed = datetime.strptime(raw.strip(), TIME_INPUT_FORMAT)
    except ValueError:
        return None
    return parsed.strftime(TIME_INPUT_FORMAT)


def _extract_reminder_id(data: str) -> int | None:
    parts = data.split(":")
    if len(parts) != 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def _format_reminder(reminder: ReminderRecord, timezone: ZoneInfo) -> str:
    if reminder.kind == "once" and reminder.run_at is not None:
        run_at = reminder.run_at.astimezone(timezone).strftime(DATETIME_INPUT_FORMAT)
        return f"#{reminder.reminder_id} - разовое - {run_at}\n- {reminder.text}"
    if reminder.kind == "daily" and reminder.daily_time is not None:
        return f"#{reminder.reminder_id} - каждый день - {reminder.daily_time} (МСК)\n- {reminder.text}"
    return f"#{reminder.reminder_id} - неизвестный формат\n- {reminder.text}"

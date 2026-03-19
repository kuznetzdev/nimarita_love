from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, Message

from nimarita.repositories.ui import EphemeralMessageRepository, UIPanelRepository

logger = logging.getLogger(__name__)

DASHBOARD_PANEL_KEY = "dashboard"


class TelegramUI:
    def __init__(
        self,
        *,
        bot: Bot,
        panels: UIPanelRepository,
        ephemeral: EphemeralMessageRepository,
    ) -> None:
        self._bot = bot
        self._panels = panels
        self._ephemeral = ephemeral

    @property
    def bot(self) -> Bot:
        return self._bot

    async def upsert_dashboard(
        self,
        *,
        user_id: int,
        chat_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> int:
        panel = await self._panels.get_panel(user_id=user_id, panel_key=DASHBOARD_PANEL_KEY)
        if panel is not None:
            edited = await self.safe_edit_message(
                chat_id=panel.chat_id,
                message_id=panel.message_id,
                text=text,
                reply_markup=reply_markup,
            )
            if edited:
                return panel.message_id

        message = await self._bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        now = datetime.now(tz=UTC)
        await self._panels.upsert_panel(
            user_id=user_id,
            panel_key=DASHBOARD_PANEL_KEY,
            chat_id=chat_id,
            message_id=int(message.message_id),
            now=now,
        )
        return int(message.message_id)

    async def safe_edit_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> bool:
        try:
            await self._bot.edit_message_text(
                chat_id=chat_id,
                message_id=message_id,
                text=text,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return True
        except Exception as error:
            if _is_message_not_modified(error):
                return True
            logger.debug("Could not edit message chat_id=%s message_id=%s: %s", chat_id, message_id, error)
            return False

    async def safe_edit_callback_message(
        self,
        *,
        message: Message,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        try:
            await message.edit_text(text=text, reply_markup=reply_markup, disable_web_page_preview=True)
            return True
        except Exception as error:
            if _is_message_not_modified(error):
                return True
            logger.debug("Could not edit callback message chat_id=%s message_id=%s: %s", message.chat.id, message.message_id, error)
            return False


    async def safe_edit_callback_reply_markup(
        self,
        *,
        message: Message,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        try:
            await message.edit_reply_markup(reply_markup=reply_markup)
            return True
        except Exception as error:
            if _is_message_not_modified(error):
                return True
            logger.debug("Could not edit callback reply_markup chat_id=%s message_id=%s: %s", message.chat.id, message.message_id, error)
            return False

    async def send_ephemeral(
        self,
        *,
        chat_id: int,
        text: str,
        seconds: int,
        kind: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> int:
        message = await self._bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            disable_web_page_preview=True,
        )
        await self.schedule_delete(
            chat_id=chat_id,
            message_id=int(message.message_id),
            seconds=seconds,
            kind=kind,
        )
        return int(message.message_id)

    async def schedule_delete(self, *, chat_id: int, message_id: int, seconds: int, kind: str) -> None:
        if seconds <= 0:
            return
        now = datetime.now(tz=UTC)
        await self._ephemeral.schedule_delete(
            chat_id=chat_id,
            message_id=message_id,
            kind=kind,
            delete_after_utc=now + timedelta(seconds=seconds),
            now=now,
        )

    async def cleanup_due_deletes(self, *, limit: int) -> int:
        now = datetime.now(tz=UTC)
        items = await self._ephemeral.claim_due(now=now, limit=limit)
        processed = 0
        for item in items:
            processed += 1
            try:
                await self._bot.delete_message(chat_id=item.chat_id, message_id=item.message_id)
            except Exception as error:
                message = str(error)
                if _delete_error_is_resolved(message):
                    await self._ephemeral.mark_deleted(item_id=item.id, now=datetime.now(tz=UTC))
                    continue
                await self._ephemeral.mark_failed(
                    item_id=item.id,
                    error_text=message or error.__class__.__name__,
                    retry_after_seconds=30 if item.attempts_count < 2 else None,
                    now=datetime.now(tz=UTC),
                )
                logger.debug(
                    "Could not delete message chat_id=%s message_id=%s: %s",
                    item.chat_id,
                    item.message_id,
                    error,
                )
            else:
                await self._ephemeral.mark_deleted(item_id=item.id, now=datetime.now(tz=UTC))
        return processed



def _is_message_not_modified(error: Exception) -> bool:
    return "message is not modified" in str(error).lower()



def _delete_error_is_resolved(message: str) -> bool:
    text = message.lower()
    return (
        "message to delete not found" in text
        or "message can't be deleted" in text
        or "message identifier is not specified" in text
    )

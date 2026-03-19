from __future__ import annotations

import logging

from aiogram import Bot

from nimarita.config import Settings
from nimarita.domain.models import CareEnvelope, ReminderEnvelope, User
from nimarita.services.care import CareReplyResult
from nimarita.telegram.keyboards import care_actions_keyboard, reminder_actions_keyboard
from nimarita.telegram.texts import (
    care_delivery_text,
    care_failed_text,
    care_sender_response_text,
    pair_confirmed_notice,
    pair_rejected_notice,
    pair_unpaired_notice,
    reminder_delivery_text,
    reminder_sender_acknowledged_text,
    reminder_sender_delivered_text,
    reminder_sender_failed_text,
    reminder_sender_snoozed_text,
)
from nimarita.telegram.ui import TelegramUI

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot: Bot, ui: TelegramUI, settings: Settings) -> None:
        self._bot = bot
        self._ui = ui
        self._settings = settings

    async def safe_message(self, chat_id: int | None, text: str, *, ephemeral: bool = False, kind: str = 'notice') -> None:
        if chat_id is None:
            return
        try:
            if ephemeral:
                await self._ui.send_ephemeral(
                    chat_id=chat_id,
                    text=text,
                    seconds=self._settings.notice_message_ttl_seconds,
                    kind=kind,
                )
            else:
                await self._bot.send_message(chat_id=chat_id, text=text, disable_web_page_preview=True)
        except Exception:
            logger.exception('Failed to send Telegram notification to chat_id=%s', chat_id)

    async def send_reminder(self, envelope: ReminderEnvelope, *, app_link: str | None) -> int:
        if envelope.recipient.private_chat_id is None:
            raise RuntimeError('Recipient has no private_chat_id.')
        message = await self._bot.send_message(
            chat_id=envelope.recipient.private_chat_id,
            text=reminder_delivery_text(envelope),
            reply_markup=reminder_actions_keyboard(envelope.occurrence.id, app_link),
            disable_web_page_preview=True,
        )
        return int(message.message_id)

    async def send_care(self, envelope: CareEnvelope, *, app_link: str | None) -> int:
        if envelope.recipient.private_chat_id is None:
            raise RuntimeError('Recipient has no private_chat_id.')
        message = await self._bot.send_message(
            chat_id=envelope.recipient.private_chat_id,
            text=care_delivery_text(envelope),
            reply_markup=care_actions_keyboard(
                envelope.dispatch.id,
                category=envelope.dispatch.category,
                page=0,
                app_link=app_link,
            ),
            disable_web_page_preview=True,
        )
        return int(message.message_id)

    async def notify_reminder_delivered(self, envelope: ReminderEnvelope) -> None:
        await self.safe_message(
            envelope.creator.private_chat_id,
            reminder_sender_delivered_text(envelope),
            ephemeral=True,
            kind='reminder-delivered',
        )

    async def notify_reminder_failed(self, creator: User, recipient: User, text: str, error_text: str) -> None:
        await self.safe_message(
            creator.private_chat_id,
            reminder_sender_failed_text(recipient, text, error_text),
            ephemeral=False,
            kind='reminder-failed',
        )

    async def notify_reminder_acknowledged(self, envelope: ReminderEnvelope) -> None:
        await self.safe_message(
            envelope.creator.private_chat_id,
            reminder_sender_acknowledged_text(envelope),
            ephemeral=True,
            kind='reminder-ack',
        )

    async def notify_reminder_snoozed(self, current: ReminderEnvelope, follow_up: ReminderEnvelope) -> None:
        await self.safe_message(
            current.creator.private_chat_id,
            reminder_sender_snoozed_text(current, follow_up),
            ephemeral=True,
            kind='reminder-snooze',
        )

    async def notify_care_failed(self, sender: User, recipient: User, template_title: str, error_text: str) -> None:
        await self.safe_message(
            sender.private_chat_id,
            care_failed_text(recipient, template_title, error_text),
            ephemeral=False,
            kind='care-failed',
        )

    async def notify_care_response(self, result: CareReplyResult) -> None:
        if result.envelope.sender.private_chat_id is None:
            return
        try:
            await self._ui.send_ephemeral(
                chat_id=result.envelope.sender.private_chat_id,
                text=care_sender_response_text(result),
                seconds=self._settings.care_sender_notice_ttl_seconds,
                kind='care-response',
            )
        except Exception:
            logger.exception('Failed to send care response notice to sender chat_id=%s', result.envelope.sender.private_chat_id)

    async def notify_pair_confirmed(self, inviter: User, invitee: User) -> None:
        await self.safe_message(inviter.private_chat_id, pair_confirmed_notice(invitee), ephemeral=True, kind='pair-confirm')
        await self.safe_message(invitee.private_chat_id, pair_confirmed_notice(inviter), ephemeral=True, kind='pair-confirm')

    async def notify_pair_rejected(self, inviter: User, rejector: User) -> None:
        await self.safe_message(inviter.private_chat_id, pair_rejected_notice(rejector), ephemeral=True, kind='pair-reject')

    async def notify_pair_closed(self, actor: User, partner: User) -> None:
        await self.safe_message(partner.private_chat_id, pair_unpaired_notice(actor), ephemeral=True, kind='pair-unpair')

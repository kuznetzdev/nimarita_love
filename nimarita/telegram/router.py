from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, Message

from nimarita.config import Settings
from nimarita.domain.enums import RelationshipRole
from nimarita.domain.errors import AccessDeniedError, ConflictError, NotFoundError, ValidationError
from nimarita.domain.models import DashboardState, TelegramUserSnapshot
from nimarita.services.care import CareService
from nimarita.services.pairing import PairingService
from nimarita.services.reminders import ReminderService
from nimarita.services.users import UserService
from nimarita.telegram.keyboards import (
    care_actions_keyboard,
    care_command_keyboard,
    confirm_unpair_keyboard,
    dashboard_keyboard,
    invite_preview_keyboard,
    main_keyboard,
    profile_keyboard,
    remind_command_keyboard,
)
from nimarita.telegram.menu import sync_private_menu_button
from nimarita.telegram.notifier import TelegramNotifier
from nimarita.telegram.texts import (
    card_hidden_short_text,
    care_hidden_text,
    care_reply_applied_text,
    care_usage_text,
    confirmation_required_text,
    dashboard_updated_text,
    help_text,
    invalid_action_text,
    invalid_invite_id_text,
    invalid_pagination_text,
    invalid_quick_reply_text,
    invalid_reminder_id_text,
    invalid_snooze_action_text,
    invite_created_text,
    invite_cancelled_short_text,
    invite_cancelled_text,
    invite_rejected_short_text,
    invite_preview_text,
    no_active_pair_text,
    open_ready_text,
    pair_closed_text,
    pair_closed_short_text,
    pair_confirmed_short_text,
    pair_confirmed_text,
    pair_link_ready_text,
    pair_rejected_text,
    pagination_updated_text,
    profile_role_saved_text,
    profile_text,
    private_chat_only_text,
    quick_reply_sent_text,
    remind_usage_text,
    reminder_done_short_text,
    reminder_snoozed_short_text,
    reminder_action_done_text,
    reminder_action_snoozed_text,
    reminder_created_text,
    status_text,
    unpair_confirmation_short_text,
    unpair_confirmation_text,
    welcome_text,
)
from nimarita.telegram.ui import TelegramUI

logger = logging.getLogger(__name__)

REMIND_ARGS_RE = re.compile(r'^\s*(\d{4}-\d{2}-\d{2})(?:[T\s]+)(\d{2}:\d{2})\s+(.+)$')



def build_router(
    *,
    settings: Settings,
    user_service: UserService,
    pairing_service: PairingService,
    reminder_service: ReminderService,
    care_service: CareService,
    notifier: TelegramNotifier,
    ui: TelegramUI,
) -> Router:
    router = Router(name='nimarita')

    async def _register_user_from_message(message: Message) -> TelegramUserSnapshot:
        if message.chat.type != 'private':
            raise ValidationError(private_chat_only_text())
        user = message.from_user
        if user is None:
            raise RuntimeError('Telegram message has no from_user.')
        snapshot = TelegramUserSnapshot(
            telegram_user_id=user.id,
            chat_id=message.chat.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
        )
        await user_service.ensure_bot_user(snapshot)
        return snapshot

    async def _register_user_from_callback(callback: CallbackQuery) -> TelegramUserSnapshot:
        chat_id = callback.message.chat.id if callback.message is not None else None
        snapshot = TelegramUserSnapshot(
            telegram_user_id=callback.from_user.id,
            chat_id=chat_id,
            username=callback.from_user.username,
            first_name=callback.from_user.first_name,
            last_name=callback.from_user.last_name,
            language_code=callback.from_user.language_code,
        )
        await user_service.ensure_bot_user(snapshot)
        return snapshot

    async def _render_dashboard(telegram_user_id: int, chat_id: int) -> DashboardState:
        state = await pairing_service.get_dashboard(telegram_user_id)
        if state.user.private_chat_id is not None:
            try:
                await sync_private_menu_button(
                    ui.bot,
                    chat_id=state.user.private_chat_id,
                    state=state,
                    settings=settings,
                )
            except Exception:
                logger.exception('Failed to sync menu button for chat_id=%s', state.user.private_chat_id)
        await ui.upsert_dashboard(
            user_id=state.user.id,
            chat_id=chat_id,
            text=status_text(state),
            reply_markup=dashboard_keyboard(state, settings.webapp_public_url),
        )
        return state

    async def _send_transient(chat_id: int, text: str, *, kind: str, seconds: int | None = None, reply_markup=None) -> None:
        await ui.send_ephemeral(
            chat_id=chat_id,
            text=text,
            seconds=seconds or settings.notice_message_ttl_seconds,
            kind=kind,
            reply_markup=reply_markup,
        )

    async def _handle_message_error(message: Message, error: Exception) -> None:
        await _send_transient(message.chat.id, str(error), kind='error')

    async def _handle_callback_error(callback: CallbackQuery, error: Exception) -> None:
        try:
            await callback.answer(str(error), show_alert=True)
        except Exception:
            logger.debug('Failed to answer callback with error', exc_info=True)

    @router.message(CommandStart())
    async def command_start(message: Message, command: CommandObject | None = None) -> None:
        if message.chat.type != 'private':
            await message.answer(private_chat_only_text())
            return
        try:
            snapshot = await _register_user_from_message(message)
        except (AccessDeniedError, ValidationError) as error:
            await message.answer(str(error))
            return
        user = await user_service.get_by_telegram_user_id(snapshot.telegram_user_id)
        assert user is not None
        await _send_transient(message.chat.id, welcome_text(user), kind='welcome', seconds=settings.welcome_message_ttl_seconds)
        await message.answer(
            'Нажми кнопку ниже, чтобы сразу открыть приложение. Там доступны пара, напоминания и заботливые сообщения.',
            reply_markup=main_keyboard(settings.webapp_public_url),
            disable_web_page_preview=True,
        )

        payload = (command.args or '').strip() if command is not None else ''
        if payload.startswith('invite_'):
            raw_token = payload.removeprefix('invite_')
            try:
                preview = await pairing_service.preview_invite(snapshot.telegram_user_id, raw_token)
            except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
                await _send_transient(message.chat.id, str(error), kind='invite-preview-error')
            else:
                await message.answer(
                    invite_preview_text(preview),
                    reply_markup=invite_preview_keyboard(preview.invite.id, settings.webapp_public_url),
                    disable_web_page_preview=True,
                )
        await _render_dashboard(snapshot.telegram_user_id, message.chat.id)

    @router.message(Command('help'))
    async def command_help(message: Message) -> None:
        await _send_transient(
            message.chat.id,
            help_text(),
            kind='help',
            seconds=max(settings.notice_message_ttl_seconds, 25),
        )

    @router.message(Command('open'))
    async def command_open(message: Message) -> None:
        try:
            snapshot = await _register_user_from_message(message)
        except (AccessDeniedError, ValidationError) as error:
            await message.answer(str(error))
            return
        await _render_dashboard(snapshot.telegram_user_id, message.chat.id)
        await _send_transient(
            message.chat.id,
            open_ready_text(),
            kind='open',
            reply_markup=main_keyboard(settings.webapp_public_url),
        )

    @router.message(Command('status'))
    async def command_status(message: Message) -> None:
        try:
            snapshot = await _register_user_from_message(message)
        except (AccessDeniedError, ValidationError) as error:
            await message.answer(str(error))
            return
        await _render_dashboard(snapshot.telegram_user_id, message.chat.id)

    @router.message(Command('profile'))
    async def command_profile(message: Message) -> None:
        try:
            snapshot = await _register_user_from_message(message)
        except (AccessDeniedError, ValidationError) as error:
            await message.answer(str(error))
            return
        user = await user_service.get_by_telegram_user_id(snapshot.telegram_user_id)
        assert user is not None
        await message.answer(
            profile_text(user),
            reply_markup=profile_keyboard(user.relationship_role),
            disable_web_page_preview=True,
        )

    @router.message(Command('pair'))
    async def command_pair(message: Message) -> None:
        try:
            snapshot = await _register_user_from_message(message)
            result = await pairing_service.create_invite(snapshot.telegram_user_id)
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_message_error(message, error)
            return
        await message.answer(invite_created_text(result.links), disable_web_page_preview=True)
        await _render_dashboard(snapshot.telegram_user_id, message.chat.id)

    @router.message(Command('unpair'))
    async def command_unpair(message: Message) -> None:
        try:
            snapshot = await _register_user_from_message(message)
            state = await pairing_service.get_dashboard(snapshot.telegram_user_id)
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_message_error(message, error)
            return
        if state.active_pair is None:
            await _send_transient(message.chat.id, no_active_pair_text(), kind='unpair-empty')
            return
        await _send_transient(
            message.chat.id,
            unpair_confirmation_text(),
            kind='unpair-confirm',
            seconds=45,
            reply_markup=confirm_unpair_keyboard(),
        )

    @router.message(Command('care'))
    async def command_care(message: Message) -> None:
        try:
            snapshot = await _register_user_from_message(message)
        except (AccessDeniedError, ValidationError) as error:
            await message.answer(str(error))
            return
        await _render_dashboard(snapshot.telegram_user_id, message.chat.id)
        await _send_transient(
            message.chat.id,
            care_usage_text(),
            kind='care-usage',
            seconds=max(settings.notice_message_ttl_seconds, 25),
            reply_markup=care_command_keyboard(settings.webapp_public_url),
        )

    @router.message(Command('remind'))
    async def command_remind(message: Message, command: CommandObject | None = None) -> None:
        try:
            snapshot = await _register_user_from_message(message)
        except (AccessDeniedError, ValidationError) as error:
            await message.answer(str(error))
            return
        args = (command.args or '').strip() if command is not None else ''
        if not args:
            await _send_transient(
                message.chat.id,
                remind_usage_text(),
                kind='remind-usage',
                seconds=max(settings.notice_message_ttl_seconds, 25),
                reply_markup=remind_command_keyboard(settings.webapp_public_url),
            )
            return
        match = REMIND_ARGS_RE.match(args)
        if not match:
            await _send_transient(
                message.chat.id,
                remind_usage_text(),
                kind='remind-usage',
                seconds=max(settings.notice_message_ttl_seconds, 25),
                reply_markup=remind_command_keyboard(settings.webapp_public_url),
            )
            return
        date_part, time_part, text = match.groups()
        user = await user_service.get_by_telegram_user_id(snapshot.telegram_user_id)
        timezone = user.timezone if user is not None else settings.default_timezone
        try:
            envelope = await reminder_service.create_one_time_reminder(
                telegram_user_id=snapshot.telegram_user_id,
                text=text,
                scheduled_for_local=f'{date_part}T{time_part}',
                timezone=timezone,
            )
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_message_error(message, error)
            return
        await _send_transient(
            message.chat.id,
            reminder_created_text(envelope),
            kind='remind-created',
            seconds=max(settings.notice_message_ttl_seconds, 18),
        )
        await _render_dashboard(snapshot.telegram_user_id, message.chat.id)

    @router.callback_query(F.data == 'pair:create')
    async def callback_create_pair(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            snapshot = await _register_user_from_callback(callback)
            result = await pairing_service.create_invite(snapshot.telegram_user_id)
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(pair_link_ready_text())
        await callback.message.answer(invite_created_text(result.links), disable_web_page_preview=True)
        await _render_dashboard(snapshot.telegram_user_id, callback.message.chat.id)

    @router.callback_query(F.data == 'invite:cancel_outgoing')
    async def callback_cancel_outgoing_invite(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            snapshot = await _register_user_from_callback(callback)
            await pairing_service.cancel_outgoing_invite(snapshot.telegram_user_id)
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(invite_cancelled_short_text())
        await _send_transient(
            callback.message.chat.id,
            invite_cancelled_text(),
            kind='invite-cancelled',
        )
        await _render_dashboard(snapshot.telegram_user_id, callback.message.chat.id)

    @router.callback_query(F.data == 'pair:status')
    async def callback_status(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            snapshot = await _register_user_from_callback(callback)
            await _render_dashboard(snapshot.telegram_user_id, callback.message.chat.id)
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(dashboard_updated_text())

    @router.callback_query(F.data == 'profile:open')
    async def callback_profile_open(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            snapshot = await _register_user_from_callback(callback)
            user = await user_service.get_by_telegram_user_id(snapshot.telegram_user_id)
            assert user is not None
        except (AccessDeniedError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await ui.safe_edit_callback_message(
            message=callback.message,
            text=profile_text(user),
            reply_markup=profile_keyboard(user.relationship_role),
        )
        await callback.answer('Профиль открыт')

    @router.callback_query(F.data.startswith('profile:set:'))
    async def callback_profile_set(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        role_text = (callback.data or '').split(':')[-1]
        try:
            role = RelationshipRole(role_text)
            snapshot = await _register_user_from_callback(callback)
            user = await user_service.set_relationship_role(snapshot.telegram_user_id, role)
        except ValueError:
            await callback.answer('Некорректная роль', show_alert=True)
            return
        except (AccessDeniedError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await ui.safe_edit_callback_message(
            message=callback.message,
            text=profile_text(user),
            reply_markup=profile_keyboard(user.relationship_role),
        )
        await _render_dashboard(snapshot.telegram_user_id, callback.message.chat.id)
        await callback.answer(profile_role_saved_text(user.relationship_role_label))

    @router.callback_query(F.data == 'pair:ask_unpair')
    async def callback_ask_unpair(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            snapshot = await _register_user_from_callback(callback)
            state = await pairing_service.get_dashboard(snapshot.telegram_user_id)
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        if state.active_pair is None:
            await callback.answer(no_active_pair_text(), show_alert=True)
            return
        await callback.answer(confirmation_required_text())
        await _send_transient(
            callback.message.chat.id,
            unpair_confirmation_short_text(),
            kind='unpair-confirm',
            seconds=45,
            reply_markup=confirm_unpair_keyboard(),
        )

    @router.callback_query(F.data.startswith('invite:accept:'))
    async def callback_accept_invite(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            invite_id = int((callback.data or '').split(':')[-1])
            snapshot = await _register_user_from_callback(callback)
            _pair, inviter, invitee = await pairing_service.accept_invite_by_id(snapshot.telegram_user_id, invite_id)
        except ValueError:
            await callback.answer(invalid_invite_id_text(), show_alert=True)
            return
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(pair_confirmed_short_text())
        await ui.safe_edit_callback_message(message=callback.message, text=pair_confirmed_text(inviter), reply_markup=None)
        await ui.schedule_delete(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            seconds=settings.action_message_ttl_seconds,
            kind='invite-accept-result',
        )
        await notifier.notify_pair_confirmed(inviter, invitee)
        await _render_dashboard(snapshot.telegram_user_id, callback.message.chat.id)

    @router.callback_query(F.data.startswith('invite:reject:'))
    async def callback_reject_invite(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            invite_id = int((callback.data or '').split(':')[-1])
            snapshot = await _register_user_from_callback(callback)
            _invite, inviter, rejector = await pairing_service.reject_invite_by_id(snapshot.telegram_user_id, invite_id)
        except ValueError:
            await callback.answer(invalid_invite_id_text(), show_alert=True)
            return
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(invite_rejected_short_text())
        await ui.safe_edit_callback_message(message=callback.message, text=pair_rejected_text(inviter), reply_markup=None)
        await ui.schedule_delete(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            seconds=settings.action_message_ttl_seconds,
            kind='invite-reject-result',
        )
        await notifier.notify_pair_rejected(inviter, rejector)
        await _render_dashboard(snapshot.telegram_user_id, callback.message.chat.id)

    @router.callback_query(F.data == 'pair:confirm_unpair')
    async def callback_confirm_unpair(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            snapshot = await _register_user_from_callback(callback)
            _pair, actor, partner = await pairing_service.unpair(snapshot.telegram_user_id)
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(pair_closed_short_text())
        await ui.safe_edit_callback_message(message=callback.message, text=pair_closed_text(actor), reply_markup=None)
        await ui.schedule_delete(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            seconds=settings.action_message_ttl_seconds,
            kind='unpair-result',
        )
        await notifier.notify_pair_closed(actor, partner)
        await _render_dashboard(snapshot.telegram_user_id, callback.message.chat.id)

    @router.callback_query(F.data.startswith('reminder:done:'))
    async def callback_reminder_done(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        try:
            occurrence_id = int((callback.data or '').split(':')[-1])
            snapshot = await _register_user_from_callback(callback)
            envelope = await reminder_service.acknowledge(
                telegram_user_id=snapshot.telegram_user_id,
                occurrence_id=occurrence_id,
            )
        except ValueError:
            await callback.answer(invalid_reminder_id_text(), show_alert=True)
            return
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(reminder_done_short_text())
        await ui.safe_edit_callback_message(
            message=callback.message,
            text=reminder_action_done_text(envelope),
            reply_markup=None,
        )
        await ui.schedule_delete(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            seconds=settings.action_message_ttl_seconds,
            kind='reminder-done-result',
        )
        await notifier.notify_reminder_acknowledged(envelope)

    @router.callback_query(F.data.startswith('reminder:snooze:'))
    async def callback_reminder_snooze(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        parts = (callback.data or '').split(':')
        if len(parts) < 4:
            await callback.answer(invalid_snooze_action_text(), show_alert=True)
            return
        try:
            occurrence_id = int(parts[2])
            minutes = int(parts[3])
            snapshot = await _register_user_from_callback(callback)
            current, follow_up = await reminder_service.snooze(
                telegram_user_id=snapshot.telegram_user_id,
                occurrence_id=occurrence_id,
                minutes=minutes,
            )
        except ValueError:
            await callback.answer(invalid_snooze_action_text(), show_alert=True)
            return
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(reminder_snoozed_short_text())
        await ui.safe_edit_callback_message(
            message=callback.message,
            text=reminder_action_snoozed_text(current, follow_up),
            reply_markup=None,
        )
        await ui.schedule_delete(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            seconds=settings.action_message_ttl_seconds,
            kind='reminder-snooze-result',
        )
        await notifier.notify_reminder_snoozed(current, follow_up)

    @router.callback_query(F.data.startswith('care:page:'))
    async def callback_care_page(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        parts = (callback.data or '').split(':')
        if len(parts) < 4:
            await callback.answer(invalid_pagination_text(), show_alert=True)
            return
        try:
            dispatch_id = int(parts[2])
            page = int(parts[3])
            snapshot = await _register_user_from_callback(callback)
            envelope = await care_service.get_dispatch_for_recipient_action(
                telegram_user_id=snapshot.telegram_user_id,
                dispatch_id=dispatch_id,
            )
            await ui.safe_edit_callback_reply_markup(
                message=callback.message,
                reply_markup=care_actions_keyboard(
                    dispatch_id,
                    category=envelope.dispatch.category,
                    page=page,
                    app_link=settings.direct_main_app_link,
                ),
            )
        except ValueError:
            await callback.answer(invalid_pagination_text(), show_alert=True)
            return
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(pagination_updated_text())

    @router.callback_query(F.data.startswith('care:reply:'))
    async def callback_care_reply(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        parts = (callback.data or '').split(':')
        if len(parts) < 4:
            await callback.answer(invalid_quick_reply_text(), show_alert=True)
            return
        try:
            dispatch_id = int(parts[2])
            reply_code = parts[3]
            snapshot = await _register_user_from_callback(callback)
            result = await care_service.register_quick_reply(
                telegram_user_id=snapshot.telegram_user_id,
                dispatch_id=dispatch_id,
                reply_code=reply_code,
            )
        except ValueError:
            await callback.answer(invalid_quick_reply_text(), show_alert=True)
            return
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(quick_reply_sent_text())
        await ui.safe_edit_callback_message(
            message=callback.message,
            text=care_reply_applied_text(result),
            reply_markup=None,
        )
        await ui.schedule_delete(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            seconds=settings.action_message_ttl_seconds,
            kind='care-reply-result',
        )
        await notifier.notify_care_response(result)

    @router.callback_query(F.data.startswith('care:hide:'))
    async def callback_care_hide(callback: CallbackQuery) -> None:
        if callback.message is None:
            await callback.answer()
            return
        parts = (callback.data or '').split(':')
        if len(parts) < 3:
            await callback.answer(invalid_action_text(), show_alert=True)
            return
        try:
            dispatch_id = int(parts[2])
            snapshot = await _register_user_from_callback(callback)
            envelope = await care_service.get_dispatch_for_recipient_action(
                telegram_user_id=snapshot.telegram_user_id,
                dispatch_id=dispatch_id,
            )
        except ValueError:
            await callback.answer(invalid_action_text(), show_alert=True)
            return
        except (AccessDeniedError, ConflictError, NotFoundError, ValidationError) as error:
            await _handle_callback_error(callback, error)
            return
        await callback.answer(card_hidden_short_text())
        await ui.safe_edit_callback_message(
            message=callback.message,
            text=care_hidden_text(envelope),
            reply_markup=None,
        )
        await ui.schedule_delete(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
            seconds=settings.action_message_ttl_seconds,
            kind='care-hidden-result',
        )

    return router

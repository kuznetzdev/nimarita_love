from __future__ import annotations

from datetime import UTC

from nimarita.domain.models import CareEnvelope, DashboardState, PairInvitePreview, ReminderEnvelope, User
from nimarita.infra.links import InviteLinks
from nimarita.services.care import CareReplyResult


def private_chat_only_text() -> str:
    return 'Эта версия работает только в личном чате с ботом.'


def welcome_text(user: User) -> str:
    return (
        f'{user.display_name}, Nimarita уже работает как парный продукт 1↔1.\n\n'
        'Здесь есть подтверждённые пары, напоминания, слой заботы с каталогом шаблонов, живые кнопки действий '
        'и автоочистка служебных карточек, чтобы чат оставался удобным.'
    )


def help_text() -> str:
    return (
        'Команды:\n'
        '/start — зарегистрировать пользователя и поднять панель\n'
        '/open — открыть мини-приложение\n'
        '/pair — создать новое приглашение в пару\n'
        '/status — обновить текущее состояние пары\n'
        '/remind YYYY-MM-DD HH:MM текст — быстрый резервный сценарий для одноразового напоминания\n'
        '/care — открыть слой заботы и каталог шаблонов\n'
        '/unpair — завершить активную пару'
    )


def status_text(state: DashboardState) -> str:
    if state.mode == 'active' and state.partner is not None:
        return (
            '💖 Активная пара подтверждена\n\n'
            f'Партнёр: {state.partner.display_name}\n'
            'Можно ставить напоминания и отправлять сообщения заботы из мини-приложения. '
            'У доставленных карточек бот редактирует сообщение после действия и убирает его автоматически.'
        )
    if state.mode == 'incoming_invite' and state.incoming_inviter is not None and state.incoming_invite is not None:
        return (
            '💌 Есть входящее приглашение\n\n'
            f'От: {state.incoming_inviter.display_name}\n'
            f'Действует до: {state.incoming_invite.expires_at.astimezone().strftime("%d.%m.%Y %H:%M %Z")}'
        )
    if state.mode == 'outgoing_invite' and state.outgoing_invite is not None:
        return (
            '📨 Есть исходящее приглашение\n\n'
            f'Действует до: {state.outgoing_invite.expires_at.astimezone().strftime("%d.%m.%Y %H:%M %Z")}\n'
            'Можно выпустить новое приглашение — предыдущее автоматически устареет.'
        )
    return 'Пары пока нет. Создай одноразовое приглашение и отправь его будущему партнёру.'


def invite_created_text(links: InviteLinks) -> str:
    mini_app_line = f'Ссылка для мини-приложения: {links.mini_app_link}\n' if links.mini_app_link else ''
    return (
        'Приглашение создано. Отправь одну из ссылок партнёру.\n\n'
        f'Ссылка для бота: {links.bot_start_link}\n'
        f'{mini_app_line}'
        'После подтверждения связь станет эксклюзивной 1↔1.'
    )


def invite_preview_text(preview: PairInvitePreview) -> str:
    return (
        f'{preview.inviter.display_name} приглашает тебя создать подтверждённую пару 1↔1.\n\n'
        'После подтверждения другие активные партнёры будут недоступны до разрыва текущей пары.'
    )


def pair_confirmed_text(partner: User) -> str:
    return f'✅ Пара подтверждена с {partner.display_name}.'


def pair_rejected_text(partner: User) -> str:
    return f'❌ Приглашение отклонено. Инициатор: {partner.display_name}.'


def pair_closed_text(actor: User) -> str:
    return f'Связь завершена по действию пользователя {actor.display_name}.'


def pair_confirmed_notice(partner: User) -> str:
    return f'Пара подтверждена с {partner.display_name} 💖'


def pair_rejected_notice(partner: User) -> str:
    return f'Приглашение отклонено: {partner.display_name}.'


def pair_unpaired_notice(actor: User) -> str:
    return f'Пара завершена пользователем {actor.display_name}.'


def reminder_delivery_text(envelope: ReminderEnvelope) -> str:
    local_dt = envelope.occurrence.scheduled_at_utc.astimezone(UTC).strftime('%d.%m.%Y %H:%M UTC')
    return (
        '⏰ Напоминание от партнёра\n\n'
        f'От: {envelope.creator.display_name}\n'
        f'Запланировано на: {local_dt}\n\n'
        f'{envelope.occurrence.text}'
    )


def reminder_created_text(envelope: ReminderEnvelope) -> str:
    local_dt = envelope.occurrence.scheduled_at_utc.astimezone().strftime('%d.%m.%Y %H:%M %Z')
    return f'Напоминание для {envelope.recipient.display_name} поставлено на {local_dt}.'


def reminder_cancelled_text(envelope: ReminderEnvelope) -> str:
    return f'Напоминание для {envelope.recipient.display_name} отменено.'


def reminder_sender_delivered_text(envelope: ReminderEnvelope) -> str:
    return f'Напоминание для {envelope.recipient.display_name} доставлено.'


def reminder_sender_failed_text(recipient: User, text: str, error_text: str) -> str:
    return (
        f'Не удалось доставить напоминание для {recipient.display_name}.\n\n'
        f'Текст: {text}\n'
        f'Причина: {error_text}'
    )


def reminder_sender_acknowledged_text(envelope: ReminderEnvelope) -> str:
    return f'{envelope.recipient.display_name} отметил(а) напоминание как выполненное ✅'


def reminder_sender_snoozed_text(current: ReminderEnvelope, follow_up: ReminderEnvelope) -> str:
    local_dt = follow_up.occurrence.scheduled_at_utc.astimezone().strftime('%d.%m.%Y %H:%M %Z')
    return f'{current.recipient.display_name} отложил(а) напоминание. Новый слот: {local_dt}.'


def reminder_action_done_text(envelope: ReminderEnvelope) -> str:
    return (
        '✅ Напоминание отмечено как выполненное\n\n'
        f'От: {envelope.creator.display_name}\n'
        f'{envelope.occurrence.text}'
    )


def reminder_action_snoozed_text(current: ReminderEnvelope, follow_up: ReminderEnvelope) -> str:
    local_dt = follow_up.occurrence.scheduled_at_utc.astimezone().strftime('%d.%m.%Y %H:%M %Z')
    return (
        '⏰ Напоминание отложено\n\n'
        f'Новый слот: {local_dt}\n'
        f'{current.occurrence.text}'
    )


def care_delivery_text(envelope: CareEnvelope) -> str:
    return (
        '💌 Сообщение заботы от партнёра\n\n'
        f'От: {envelope.sender.display_name}\n'
        f'{envelope.dispatch.emoji} {envelope.dispatch.title}\n\n'
        f'{envelope.dispatch.body}'
    )


def care_sent_text(envelope: CareEnvelope) -> str:
    return f'Сообщение заботы для {envelope.recipient.display_name} отправлено.'


def care_failed_text(recipient: User, template_title: str, error_text: str) -> str:
    return (
        f'Не удалось доставить сообщение заботы для {recipient.display_name}.\n\n'
        f'Шаблон: {template_title}\n'
        f'Причина: {error_text}'
    )


def care_reply_applied_text(result: CareReplyResult) -> str:
    return (
        '💖 Ответ отправлен\n\n'
        f'Для: {result.envelope.sender.display_name}\n'
        f'{result.reply.emoji} {result.reply.title}\n\n'
        f'{result.reply.body}'
    )


def care_hidden_text(envelope: CareEnvelope) -> str:
    return (
        '🫥 Карточка скрыта\n\n'
        f'От: {envelope.sender.display_name}\n'
        'История останется доступна в мини-приложении.'
    )


def care_sender_response_text(result: CareReplyResult) -> str:
    return (
        f'{result.envelope.recipient.display_name} ответил(а) на твоё сообщение заботы.\n\n'
        f'{result.reply.emoji} {result.reply.title}\n'
        f'{result.reply.body}'
    )


def care_usage_text() -> str:
    return (
        'Слой заботы теперь доступен в мини-приложении.\n\n'
        'Там есть большой каталог шаблонов по категориям, история отправок и быстрые ответы через inline-кнопки.'
    )


def remind_usage_text() -> str:
    return (
        'Формат резервной команды:\n'
        '/remind 2026-03-19 21:30 Купить цветы\n\n'
        'Дата и время интерпретируются в твоём текущем часовом поясе.'
    )


def open_ready_text() -> str:
    return 'Точка входа готова. Можно сразу открыть мини-приложение.'


def no_active_pair_text() -> str:
    return 'Активной пары нет.'


def unpair_confirmation_text() -> str:
    return 'Подтверди разрыв пары. Это действие остановит все парные сценарии до нового подтверждения.'


def unpair_confirmation_short_text() -> str:
    return 'Подтверди разрыв пары. После этого напоминания и парные действия остановятся.'


def pair_link_ready_text() -> str:
    return 'Ссылка для пары готова'


def dashboard_updated_text() -> str:
    return 'Панель обновлена'


def confirmation_required_text() -> str:
    return 'Нужно подтверждение'


def invalid_invite_id_text() -> str:
    return 'Некорректный идентификатор приглашения'


def pair_confirmed_short_text() -> str:
    return 'Пара подтверждена'


def invite_rejected_short_text() -> str:
    return 'Приглашение отклонено'


def pair_closed_short_text() -> str:
    return 'Пара завершена'


def invalid_reminder_id_text() -> str:
    return 'Некорректный идентификатор напоминания'


def reminder_done_short_text() -> str:
    return 'Готово ✅'


def invalid_snooze_action_text() -> str:
    return 'Некорректное действие отложенного напоминания'


def reminder_snoozed_short_text() -> str:
    return 'Отложено'


def invalid_pagination_text() -> str:
    return 'Некорректная пагинация'


def pagination_updated_text() -> str:
    return 'Показал другие ответы'


def invalid_quick_reply_text() -> str:
    return 'Некорректный быстрый ответ'


def quick_reply_sent_text() -> str:
    return 'Ответ отправлен 💖'


def invalid_action_text() -> str:
    return 'Некорректное действие'


def card_hidden_short_text() -> str:
    return 'Карточка скрыта'

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from nimarita.catalog import get_quick_reply_pages
from nimarita.domain.enums import RelationshipRole
from nimarita.domain.models import DashboardState



def dashboard_keyboard(state: DashboardState, webapp_url: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if webapp_url:
        builder.row(
            InlineKeyboardButton(
                text='Открыть пространство 💖',
                web_app=WebAppInfo(url=webapp_url),
            )
        )

    if state.mode == 'active':
        builder.row(InlineKeyboardButton(text='Завершить связь', callback_data='pair:ask_unpair'))
        builder.row(InlineKeyboardButton(text='Обновить', callback_data='pair:status'))
    elif state.mode == 'incoming_invite' and state.incoming_invite is not None:
        builder.row(InlineKeyboardButton(text='Подтвердить пару 💌', callback_data=f'invite:accept:{state.incoming_invite.id}'))
        builder.row(InlineKeyboardButton(text='Отклонить', callback_data=f'invite:reject:{state.incoming_invite.id}'))
        builder.row(InlineKeyboardButton(text='Обновить', callback_data='pair:status'))
    else:
        builder.row(InlineKeyboardButton(text='Создать пару 💞', callback_data='pair:create'))
        builder.row(InlineKeyboardButton(text='Обновить', callback_data='pair:status'))

    builder.row(InlineKeyboardButton(text='Кто я в паре', callback_data='profile:open'))

    return builder.as_markup()



def main_keyboard(webapp_url: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if webapp_url:
        builder.row(
            InlineKeyboardButton(
                text='Открыть пространство 💖',
                web_app=WebAppInfo(url=webapp_url),
            )
        )
    builder.row(InlineKeyboardButton(text='Создать пару 💞', callback_data='pair:create'))
    builder.row(InlineKeyboardButton(text='Указать роль', callback_data='profile:open'))
    builder.row(InlineKeyboardButton(text='Статус', callback_data='pair:status'))
    return builder.as_markup()



def invite_preview_keyboard(invite_id: int, webapp_url: str | None = None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text='Подтвердить пару 💌', callback_data=f'invite:accept:{invite_id}'))
    builder.row(InlineKeyboardButton(text='Отклонить', callback_data=f'invite:reject:{invite_id}'))
    if webapp_url:
        builder.row(InlineKeyboardButton(text='Открыть мини-приложение', web_app=WebAppInfo(url=webapp_url)))
    return builder.as_markup()



def confirm_unpair_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Разорвать пару', callback_data='pair:confirm_unpair')],
            [InlineKeyboardButton(text='Отмена', callback_data='pair:status')],
        ]
    )



def reminder_actions_keyboard(occurrence_id: int, app_link: str | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text='Выполнено ✅', callback_data=f'reminder:done:{occurrence_id}'),
        InlineKeyboardButton(text='Отложить на 10 минут ⏰', callback_data=f'reminder:snooze:{occurrence_id}:10'),
    )
    if app_link:
        builder.row(InlineKeyboardButton(text='Открыть мини-приложение 💖', url=app_link))
    return builder.as_markup()



def care_actions_keyboard(dispatch_id: int, *, category: str, page: int, app_link: str | None) -> InlineKeyboardMarkup:
    pages = get_quick_reply_pages(category)
    safe_page = max(0, min(page, len(pages) - 1))
    page_items = pages[safe_page]
    builder = InlineKeyboardBuilder()

    buttons = [InlineKeyboardButton(text=f'{item.emoji} {item.title}', callback_data=f'care:reply:{dispatch_id}:{item.code}') for item in page_items]
    if len(buttons) >= 2:
        builder.row(buttons[0], buttons[1])
        if len(buttons) >= 3:
            builder.row(buttons[2])
    elif buttons:
        builder.row(*buttons)

    if len(pages) > 1:
        nav_buttons: list[InlineKeyboardButton] = []
        if safe_page > 0:
            nav_buttons.append(InlineKeyboardButton(text='⬅️ Назад', callback_data=f'care:page:{dispatch_id}:{safe_page - 1}'))
        if safe_page < len(pages) - 1:
            nav_buttons.append(InlineKeyboardButton(text='Ещё ответы ➡️', callback_data=f'care:page:{dispatch_id}:{safe_page + 1}'))
        if nav_buttons:
            builder.row(*nav_buttons)

    builder.row(InlineKeyboardButton(text='Скрыть карточку 🫥', callback_data=f'care:hide:{dispatch_id}'))
    if app_link:
        builder.row(InlineKeyboardButton(text='Открыть пространство 💖', url=app_link))
    return builder.as_markup()



def remind_command_keyboard(webapp_url: str | None) -> InlineKeyboardMarkup | None:
    if not webapp_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Открыть форму напоминания 💖', web_app=WebAppInfo(url=webapp_url))]
        ]
    )



def care_command_keyboard(webapp_url: str | None) -> InlineKeyboardMarkup | None:
    if not webapp_url:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text='Открыть заботливые сообщения 💌', web_app=WebAppInfo(url=webapp_url))]
        ]
    )


def profile_keyboard(current_role: RelationshipRole) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    woman_label = '✅ Я девушка' if current_role is RelationshipRole.WOMAN else 'Я девушка'
    man_label = '✅ Я парень' if current_role is RelationshipRole.MAN else 'Я парень'
    unspecified_label = '✅ Не указывать' if current_role is RelationshipRole.UNSPECIFIED else 'Не указывать'
    builder.row(
        InlineKeyboardButton(text=woman_label, callback_data='profile:set:woman'),
        InlineKeyboardButton(text=man_label, callback_data='profile:set:man'),
    )
    builder.row(InlineKeyboardButton(text=unspecified_label, callback_data='profile:set:unspecified'))
    return builder.as_markup()

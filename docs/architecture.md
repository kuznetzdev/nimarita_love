# Архитектура Nimarita

## 1. Целевая модель

Продукт построен вокруг подтверждённой пары **1↔1**:

- у пользователя может быть не более одной активной пары;
- действия вне активной пары запрещены;
- reminders и care-сообщения адресуются только партнёру из активной пары.

## 2. Слои

### `nimarita/domain`
Доменные enum, dataclass-модели и ошибки.

### `nimarita/infra`
Низкоуровневый SQLite runtime и генерация ссылок.

### `nimarita/repositories`
Репозитории поверх SQLite:

- users
- pairing
- reminders
- care
- audit
- ui

### `nimarita/services`
Бизнес-логика:

- access policy
- users
- pairing
- reminders
- care
- audit
- system / maintenance

### `nimarita/telegram`
Bot UX: router, клавиатуры, menu button sync, notifier, UI state.

### `nimarita/web`
Mini App backend, auth, session, health endpoints, static frontend.

### `nimarita/workers`
Фоновые циклы:

- reminder delivery
- care delivery
- cleanup transient messages
- SQLite maintenance

## 3. Runtime

Запуск идёт через `nimarita.runner`:

1. читается конфигурация;
2. поднимается SQLite;
3. создаются repositories и services;
4. строится `Dispatcher`;
5. стартуют web runtime и workers;
6. начинается polling Telegram API.

## 4. Хранение данных

Основные таблицы:

- `users`
- `pair_invites`
- `pairs`
- `reminder_rules`
- `reminder_occurrences`
- `care_templates`
- `care_dispatches`
- `audit_logs`
- `ui_panels`
- `ephemeral_messages`

## 5. Ограничения и гарантии

- SQLite режим рассчитан на один живой инстанс приложения;
- active pair 1↔1 дополнительно защищена на уровне БД триггерами и индексами;
- workers восстанавливают застрявшие `processing`-состояния на старте;
- Mini App не доверяет frontend-данным без server-side Telegram auth verification.

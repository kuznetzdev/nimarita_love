# Nimarita

Telegram-бот и Mini App для подтверждённых пар **1↔1**. Каждый пользователь может иметь только одну активную пару, а все напоминания и care-сообщения работают только внутри этой пары.

## Что внутри

- асинхронный bot runtime на `aiogram`
- Mini App backend на `aiohttp`
- SQLite с `WAL`, checkpoint, quick-check и hot-backup
- reminders с outbox/worker delivery
- care layer: каталог шаблонов, история, быстрые ответы и антиспам
- feature-flag allowlist для закрытого или открытого запуска
- health/readiness endpoints и audit log

## Финальная структура

```text
.
├── nimarita/
│   ├── app.py
│   ├── runner.py
│   ├── config.py
│   ├── logging.py
│   ├── catalog/
│   ├── domain/
│   ├── infra/
│   ├── repositories/
│   ├── services/
│   ├── telegram/
│   ├── web/
│   │   └── static/index.html
│   └── workers/
├── docs/
│   ├── architecture.md
│   └── operations.md
├── scripts/
│   └── cleanup_legacy_layout.py
├── tests/
├── .env.example
├── build_merged_zip.py
├── main.py
└── requirements.txt
```

## Быстрый запуск

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python main.py
```

Альтернативный запуск через пакет:

```bash
python -m nimarita
```

## Обязательные настройки

Минимум нужны:

- `BOT_TOKEN`
- `BOT_USERNAME`

Если нужен Mini App, также нужен внешний `WEBAPP_PUBLIC_URL` по HTTPS.

## Режимы доступа

### Открытый режим

```env
ACCESS_ALLOWLIST_ENABLED=false
```

Любой пользователь может стартовать бота и создавать пару.

### Закрытый beta-режим

```env
ACCESS_ALLOWLIST_ENABLED=true
ALLOWED_USER_IDS=123456789,987654321
```

Бот будет обслуживать только указанные Telegram user id.

## Базовый сценарий

1. Пользователь A делает `/start`
2. Пользователь B делает `/start`
3. Пользователь A делает `/pair`
4. Пользователь B открывает invite link и подтверждает пару
5. После этого доступны reminders и care layer

## Команды

- `/start`
- `/open`
- `/pair`
- `/status`
- `/remind`
- `/care`
- `/help`
- `/unpair`

## Надёжность на SQLite

Этот runtime рассчитан на **single-instance production**:

- `WAL`
- `synchronous=FULL`
- `busy_timeout`
- startup quick-check и foreign-key check
- maintenance worker
- hot-backup через SQLite backup API
- graceful checkpoint на shutdown

## Если вливаешь архив поверх старого репозитория

В архиве уже нет legacy-дубликатов, но если ты накатываешь файлы поверх старого checkout, после копирования запусти:

```bash
python scripts/cleanup_legacy_layout.py --apply
```

Скрипт удалит старые пути, которые больше не должны жить в репозитории.

## Тесты

```bash
python -m unittest discover -v
```

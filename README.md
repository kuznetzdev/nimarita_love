# Nimarita

Telegram-бот и Mini App для подтверждённых пар **1↔1**. Каждый пользователь может иметь только одну активную пару, а все напоминания и care-сообщения работают только внутри этой пары.

## Что внутри

- асинхронный bot runtime на `aiogram`
- Mini App backend на `aiohttp`
- SQLite с автоматическим выбором journal mode, checkpoint, quick-check и hot-backup
- reminders с outbox/worker delivery
- care: каталог шаблонов, история, быстрые ответы, custom-сообщения и антиспам
- роли пары: можно указать, кто девушка и кто парень
- регулярные напоминания: один раз, каждый день, по будням, раз в неделю
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
- `/profile`
- `/pair`
- `/status`
- `/remind`
- `/care`
- `/help`
- `/unpair`

## Надёжность на SQLite

Этот runtime рассчитан на **single-instance production**:

- Railway Volume для `PRODUCT_DB_PATH` и `PRODUCT_BACKUP_DIR`
- `AUTO` journal mode: локально `WAL`, на Railway Volume — `DELETE`
- `synchronous=FULL`
- `busy_timeout`
- startup quick-check и foreign-key check
- maintenance worker
- hot-backup через SQLite backup API
- graceful checkpoint на shutdown

### Railway: что обязательно сделать

1. Подключить **Volume** к сервису на Railway.
2. Не включать несколько replicas для этого сервиса с SQLite.
3. Оставить `SQLITE_JOURNAL_MODE=AUTO`.
4. Не указывать `PRODUCT_DB_PATH`, если хочешь, чтобы приложение само использовало `RAILWAY_VOLUME_MOUNT_PATH/nimarita.db`.
5. Для бэкапов оставить `PRODUCT_BACKUP_DIR` пустым или направить его в volume.

Приложение уже умеет подхватывать `RAILWAY_VOLUME_MOUNT_PATH` и складывать туда базу и backup-ы по умолчанию.

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

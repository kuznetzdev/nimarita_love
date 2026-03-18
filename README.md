# Nimarita Love Bot

Telegram-бот для пары, который помогает не терять важные бытовые и заботливые договорённости: через Telegram-чат можно быстро создавать личные разовые и ежедневные напоминания, а через Telegram Mini App — работать с расширенным интерфейсом, списком задач и completion flow.

Проект рассчитан на приватное использование: доступ ограничивается через `profiles.json`, `ALLOWED_USER_IDS` и `ALLOWED_CHAT_IDS`, а Mini App дополнительно проверяет Telegram `initData`.

## Содержание

- [Что умеет бот](#что-умеет-бот)
- [Технологический стек](#технологический-стек)
- [Структура проекта](#структура-проекта)
- [Как это работает](#как-это-работает)
- [Быстрый старт](#быстрый-старт)
- [Конфигурация](#конфигурация)
- [Запуск](#запуск)
- [Тесты](#тесты)
- [Документация](#документация)
- [Ограничения текущей реализации](#ограничения-текущей-реализации)
- [Полезные команды](#полезные-команды)

## Что умеет бот

- Создаёт разовые напоминания с датой и временем в формате `ДД.ММ.ГГГГ ЧЧ:ММ`.
- Создаёт ежедневные напоминания по времени в формате `ЧЧ:ММ`.
- Хранит активные напоминания между рестартами процесса.
- Позволяет просматривать и удалять активные напоминания из Telegram-диалога.
- Позволяет отмечать напоминания выполненными через HTTP API / Mini App.
- Поддерживает адресацию получателя через Mini App / backend API: напоминание можно отправить себе или второму участнику пары.
- Восстанавливает активные задачи после перезапуска процесса.
- Проверяет доступ по приватному реестру профилей и Telegram Mini App `initData`.
- Имеет задел под голосовые напоминания через `voice_file_id` в backend API.

## Технологический стек

- **Язык**: Python 3.11+
- **Telegram SDK**: `aiogram 3.13`
- **Планировщик**: `APScheduler 3.10`
- **HTTP / Mini App backend**: `aiohttp 3.9`
- **Хранилище**: JSON-файл `data/reminders.json`
- **Конфигурация**: `.env` через `python-dotenv`
- **Таймзоны**: `zoneinfo`, базовая зона по умолчанию `Europe/Moscow`
- **Тесты**: `unittest`
- **Frontend**: статический `webapp/index.html` без сборщика

## Структура проекта

```text
.
├── bot/
│   ├── access.py          # access control и резолвинг получателей
│   ├── handlers.py        # aiogram-router и FSM сценарии
│   ├── keyboards.py       # inline-клавиатуры Telegram
│   ├── models.py          # доменные dataclass-модели
│   ├── profiles.py        # profiles.json и пользовательский реестр
│   ├── scheduler.py       # APScheduler и доставка напоминаний
│   ├── storage.py         # JSON storage с asyncio.Lock
│   └── web_app.py         # aiohttp Mini App backend и Telegram initData verification
├── data/                  # runtime-данные (создаётся автоматически)
├── logs/                  # каталог под runtime-логи
├── scripts/
│   └── create_source_archive.py
├── tests/
│   ├── test_access.py
│   ├── test_profiles.py
│   ├── test_storage.py
│   └── test_web_app.py
├── webapp/
│   └── index.html         # Telegram Mini App UI
├── config.py              # env parsing и глобальные константы
├── main.py                # точка входа
├── profiles.example.json  # пример реестра пары
└── requirements.txt
```

## Как это работает

### Основной runtime

При запуске `main.py` приложение:

1. Загружает конфигурацию из `config.py`.
2. Инициализирует `Bot` и `Dispatcher` c `MemoryStorage`.
3. Поднимает `ReminderStorage` и читает `data/reminders.json`.
4. Запускает `ReminderScheduler`, который восстанавливает активные задания в APScheduler.
5. При `WEBAPP_ENABLED=1` поднимает `aiohttp`-сервер для Mini App.
6. Подключает aiogram-router из `bot.handlers`.
7. Запускает polling Telegram API.

### Каналы взаимодействия

- **Telegram chat flow**: создание разовых и ежедневных напоминаний через inline-меню.
- **Mini App flow**: создание, просмотр и completion напоминаний через HTTP API.

### Модель данных

В памяти и в JSON используется `ReminderRecord`:

| Поле | Назначение |
| --- | --- |
| `reminder_id` | внутренний ID напоминания |
| `chat_id` | чат/инициатор, создавший напоминание |
| `recipient_chat_id` | получатель напоминания |
| `timezone` | timezone reminder record |
| `text` | текст напоминания |
| `kind` | `once` или `daily` |
| `recurring` | флаг повторяемости |
| `run_at` | дата-время для разового напоминания |
| `daily_time` | время для ежедневного напоминания |
| `voice` / `voice_file_id` | параметры голосовой доставки |
| `last_completed_at` | когда напоминание отметили выполненным |
| `is_active` | активность записи |
| `created_at` | время создания |

## Быстрый старт

### 1. Подготовить окружение

Рекомендуемый Python: `3.11+`.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Создать `.env`

```powershell
Copy-Item .env.example .env
```

Минимально обязательна только переменная `BOT_TOKEN`, но для приватного режима нужно настроить и allowlist.

### 3. Настроить профили пары

```powershell
Copy-Item profiles.example.json profiles.json
```

После этого пропиши реальные Telegram user ID участников пары:

```json
{
  "profiles": [
    {
      "id": 111111111,
      "label": "Nick",
      "role": "boyfriend",
      "gender": "male"
    },
    {
      "id": 222222222,
      "label": "Margarette",
      "role": "girlfriend",
      "gender": "female"
    }
  ]
}
```

### 4. Запустить бота

```powershell
python main.py
```

После запуска:

- каталог `data/` будет создан автоматически;
- файл `data/reminders.json` будет создан при первой записи;
- бот начнёт polling Telegram API;
- при включённом webapp поднимется встроенный HTTP-сервер.

## Конфигурация

### Обязательные переменные

| Переменная | Описание |
| --- | --- |
| `BOT_TOKEN` | токен Telegram-бота |

### Контроль доступа

| Переменная | Описание | Пример |
| --- | --- | --- |
| `ALLOWED_USER_IDS` | список допустимых user ID через запятую | `123,456` |
| `ALLOWED_CHAT_IDS` | список допустимых chat ID через запятую | `123,456` |

Важно:

- если `profiles.json` не пустой, он становится **первичным allowlist**;
- если дополнительно заданы `ALLOWED_USER_IDS` / `ALLOWED_CHAT_IDS`, доступ проходит только их пересечение с `profiles.json`;
- Mini App использует тот же `AccessManager`, что и aiogram-часть.

### Лимиты и доставка

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `MAX_REMINDER_TEXT_LENGTH` | `1000` | лимит текста, пока используется как конфигурационная константа |
| `MAX_ACTIVE_REMINDERS_PER_CHAT` | `100` | лимит активных напоминаний на чат, пока не валидируется в handler-layer |
| `SCHEDULER_MISFIRE_GRACE_SECONDS` | `300` | grace period для APScheduler |
| `DELIVERY_RETRY_ATTEMPTS` | `2` | число повторных попыток отправки |
| `DELIVERY_COOLDOWN_SECONDS` | `1` | резерв под throttling |
| `TELEGRAM_INIT_DATA_TTL_SECONDS` | `3600` | TTL для проверки Mini App `initData` |

### Mini App runtime

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `WEBAPP_URL` | пусто | URL, который Telegram откроет по кнопке `Mini App` |
| `WEBAPP_ENABLED` | `0` | включает встроенный HTTP-сервер |
| `WEBAPP_LISTEN_HOST` | `127.0.0.1` | bind host aiohttp-сервера |
| `WEBAPP_LISTEN_PORT` | `8080` | bind port aiohttp-сервера |

Замечание по продакшену:

- `WEBAPP_URL` должен указывать на внешний HTTPS URL, доступный Telegram-клиенту;
- `WEBAPP_LISTEN_HOST/PORT` отвечают только за локальный bind процесса;
- для разработки с Mini App обычно нужен туннель вроде Cloudflare Tunnel / ngrok.

## Запуск

### Режим 1. Только бот

Подходит для простого приватного использования через диалог Telegram.

```dotenv
WEBAPP_URL=
WEBAPP_ENABLED=0
```

```powershell
python main.py
```

### Режим 2. Бот + Mini App

Подходит, если нужен web-интерфейс внутри Telegram.

```dotenv
WEBAPP_URL=https://your-public-domain.example
WEBAPP_ENABLED=1
WEBAPP_LISTEN_HOST=127.0.0.1
WEBAPP_LISTEN_PORT=8080
```

```powershell
python main.py
```

В этом режиме один и тот же процесс обслуживает:

- aiogram polling;
- планировщик APScheduler;
- aiohttp HTTP API;
- статический frontend `webapp/index.html`.

## Тесты

Проект использует стандартный `unittest`.

Запуск всех тестов:

```powershell
python -m unittest discover -s tests
```

Что покрыто тестами сейчас:

- профили и резолвинг ролей;
- access control и fallback на env allowlist;
- поведение storage при completion разовых и ежедневных задач;
- проверка Telegram `initData`.

## Документация

- [Архитектура и техническое устройство](docs/architecture.md)
- [Операционная документация](docs/operations.md)

## Ограничения текущей реализации

- Хранилище файловое: используется JSON, а не SQLite/PostgreSQL.
- Scheduler in-memory: проект рассчитан на **один экземпляр процесса**.
- FSM хранится в `MemoryStorage`, поэтому незавершённые диалоги не переживают рестарт.
- Нет webhook-режима Telegram, только polling.
- Нет миграций данных и схемы БД, потому что БД как таковой нет.
- Ограничения `MAX_REMINDER_TEXT_LENGTH` и `MAX_ACTIVE_REMINDERS_PER_CHAT` объявлены в конфиге, но пока не enforced во всех user flows.
- Голосовые напоминания поддержаны в backend API, но стандартный aiogram flow не содержит UI для их создания.

## Полезные команды

```powershell
# локальный запуск
python main.py

# прогон тестов
python -m unittest discover -s tests

# архив исходников без секретов и runtime-мусора
python scripts/create_source_archive.py
```

## Когда читать что

- Если нужно быстро поднять проект локально, начни с этого `README.md`.
- Если нужно понять внутренние связи модулей, открой [docs/architecture.md](docs/architecture.md).
- Если задача про развёртывание, эксплуатацию, бэкапы и диагностику, смотри [docs/operations.md](docs/operations.md).

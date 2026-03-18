# Эксплуатация и запуск

## 1. Режимы работы

Проект можно эксплуатировать в двух конфигурациях.

## 1.1 Bot-only

Используется только Telegram chat UI.

Подходит, если:

- Mini App не нужен;
- бот развёрнут для пары или очень маленького закрытого круга;
- хочется минимальный operational surface.

Конфигурация:

```dotenv
WEBAPP_URL=
WEBAPP_ENABLED=0
```

Запуск:

```powershell
python main.py
```

## 1.2 Bot + Mini App

Используется и chat UI, и встроенный web backend.

Подходит, если:

- нужен более быстрый ввод задач;
- нужно completion через web UI;
- нужен единый интерфейс со списком и фильтрами.

Конфигурация:

```dotenv
WEBAPP_URL=https://public-domain.example
WEBAPP_ENABLED=1
WEBAPP_LISTEN_HOST=127.0.0.1
WEBAPP_LISTEN_PORT=8080
```

Важно:

- `WEBAPP_URL` обязан быть внешним HTTPS URL;
- `WEBAPP_LISTEN_HOST/PORT` только поднимают локальный aiohttp server;
- в продакшене обычно нужен reverse proxy или туннель.

## 2. Локальный запуск

### 2.1 Установка зависимостей

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2.2 Подготовка конфигурации

```powershell
Copy-Item .env.example .env
Copy-Item profiles.example.json profiles.json
```

Заполни:

- `BOT_TOKEN`
- `ALLOWED_USER_IDS`
- при необходимости `ALLOWED_CHAT_IDS`
- реальные профили участников пары в `profiles.json`

### 2.3 Запуск

```powershell
python main.py
```

## 3. Проверка работоспособности

### 3.1 Telegram chat flow

Минимальный smoke test:

1. открыть диалог с ботом;
2. выполнить `/start`;
3. открыть `/menu`;
4. создать разовое напоминание;
5. убедиться, что оно появляется в списке;
6. дождаться доставки или удалить через inline-button.

### 3.2 Mini App flow

Если Mini App включён:

1. открыть кнопку `Mini App` в меню;
2. убедиться, что `/auth` проходит успешно;
3. создать `once` и `daily` запись;
4. проверить список активных напоминаний;
5. отметить запись выполненной.

### 3.3 Health endpoint

Для web runtime:

```powershell
Invoke-WebRequest http://127.0.0.1:8080/health
```

Ожидаемый ответ:

```json
{"status":"ok"}
```

## 4. Тесты

Запуск полного набора:

```powershell
python -m unittest discover -s tests
```

Для CI этого достаточно, потому что тесты не требуют Telegram network access.

## 5. Данные и резервное копирование

## 5.1 Что нужно сохранять

Минимальный набор stateful файлов:

- `data/reminders.json`
- `profiles.json`
- `.env` или иной источник secrets

### Что не нужно архивировать

- `logs/`
- `venv/`
- `__pycache__/`
- runtime-мусор

## 5.2 Резервное копирование вручную

Простейший вариант:

```powershell
Copy-Item data\reminders.json backups\reminders.json
Copy-Item profiles.json backups\profiles.json
```

Или использовать встроенный архиватор исходников:

```powershell
python scripts/create_source_archive.py
```

Скрипт:

- исключает `.env`, ключи и бинарные артефакты;
- исключает `data/`, `logs/`, `dist/`, `venv/`;
- создаёт ZIP в `dist/`.

Важно: этот архив подходит для передачи исходников, но **не** для полного runtime backup, потому что `data/` в него намеренно не входит.

## 6. Продакшен-рекомендации

## 6.1 systemd / service manager

Проект лучше держать под process supervisor:

- `systemd`
- NSSM / Windows Service wrapper
- Docker entrypoint supervisor

Нужно обеспечить:

- автозапуск;
- restart policy;
- логирование stdout/stderr;
- хранение `.env` вне git.

## 6.2 Reverse proxy для Mini App

Если используется Mini App, перед `aiohttp` нужен внешний HTTPS слой:

- Nginx
- Caddy
- Traefik
- Cloudflare Tunnel

Причины:

- Telegram Mini App требует доступный публичный URL;
- TLS termination лучше вынести наружу;
- встроенный aiohttp не должен напрямую экспонироваться в интернет без proxy-policy.

## 6.3 Секреты

Нельзя хранить в репозитории:

- реальный `BOT_TOKEN`
- production `.env`
- любые внешние ключи и токены

Допустимо хранить только:

- `.env.example`
- `profiles.example.json`

## 7. Ограничения эксплуатации

### 7.1 Single-instance only

Текущая архитектура не предназначена для нескольких реплик, потому что:

- storage файловый;
- scheduler in-memory;
- нет distributed lock;
- нет shared job store.

Если поднять несколько экземпляров, возможны:

- гонки записи в `reminders.json`;
- дублированная доставка;
- рассинхрон между storage и scheduler.

### 7.2 Restart semantics

После рестарта:

- активные reminders восстанавливаются из файла;
- FSM-состояния пользователей теряются;
- in-flight chat сценарии начинают заново.

### 7.3 Delivery guarantees

Сейчас delivery model — at-least-possible / best-effort внутри одного процесса:

- есть retry на `TelegramAPIError`;
- нет transactional связи между отправкой сообщения и финальной деактивацией записи;
- при аварии процесса в неудачный момент возможны повторы.

## 8. Troubleshooting

## 8.1 Бот не стартует

Проверь:

- заполнен ли `BOT_TOKEN`;
- активировано ли виртуальное окружение;
- установлены ли зависимости из `requirements.txt`.

Типовая ошибка:

```text
RuntimeError: Переменная окружения BOT_TOKEN обязательна для запуска бота.
```

## 8.2 Пользователь получает `Доступ закрыт`

Проверь:

- присутствует ли user ID в `profiles.json`;
- если задан `ALLOWED_USER_IDS`, входит ли туда user ID;
- если задан `ALLOWED_CHAT_IDS`, входит ли туда chat ID.

Практически чаще всего проблема одна из двух:

- забыли обновить `profiles.json`;
- забыли синхронизировать `.env` allowlist.

## 8.3 Mini App не проходит `/auth`

Проверь:

- Mini App открыт именно внутри Telegram, а не просто в браузере;
- `initData` не просрочен;
- URL соответствует тому, что доступно Telegram-клиенту;
- бот использует тот же `BOT_TOKEN`, для которого сгенерирован `initData`.

## 8.4 Напоминания не доставляются

Проверь:

- не заблокировал ли пользователь бота;
- активен ли процесс scheduler внутри `main.py`;
- нет ли в логах `TelegramAPIError` или `TelegramForbiddenError`;
- не ушло ли `run_at` в прошлое.

Для `once` reminders в прошлом storage автоматически деактивирует запись при восстановлении.

## 8.5 После рестарта пропал незавершённый сценарий создания

Это ожидаемо: FSM хранится в `MemoryStorage`. Напоминания при этом не теряются, теряется только промежуточное состояние диалога.

## 9. Чеклист перед деплоем

- заполнен production `.env`
- проверен `BOT_TOKEN`
- актуализирован `profiles.json`
- smoke-tested `/start`, `/menu`, create once, create daily
- при Mini App проверен внешний HTTPS URL
- сделан backup `data/reminders.json`
- выполнен `python -m unittest discover -s tests`

## 10. Что улучшать дальше с точки зрения ops

- увести storage в SQLite/PostgreSQL;
- вынести scheduler state в durable backend;
- добавить structured logging и ротацию логов;
- добавить metrics и алерты;
- оформить сервис в Docker / compose / systemd unit;
- внедрить CI для автоматического прогона тестов и линтинга.

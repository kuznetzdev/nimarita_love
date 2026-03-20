# Эксплуатация Nimarita

## 1. Установка

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## 2. Запуск

```bash
python main.py
```

или

```bash
python -m nimarita
```

## 3. Минимальная проверка

1. `/start` у пользователя A
2. `/start` у пользователя B
3. `/pair` у пользователя A
4. подтверждение invite у пользователя B
5. `/status`
6. `/remind 2030-01-01 10:00 Купить цветы`
7. `/care`

## 4. Health endpoints

- `GET /health`
- `GET /api/v1/health`
- `GET /health/live`
- `GET /api/v1/health/live`
- `GET /health/ready`
- `GET /api/v1/health/ready`

`ready` проверяет доступность БД, состояние последнего аудита и heartbeat воркеров.

## 5. SQLite режим

Для высокой надёжности в single-instance режиме используются:

- Railway Volume для БД и backup-ов
- `AUTO` journal mode: локально `WAL`, на Railway Volume — `DELETE`
- `synchronous=FULL`
- `busy_timeout`
- авто-checkpoint
- quick-check по расписанию
- hot-backup snapshots
- graceful shutdown checkpoint

### Railway checklist

1. Прикрепи `Volume` к тому же сервису, где запускается приложение.
2. Держи только **один экземпляр** сервиса, если используется SQLite.
3. Оставь `SQLITE_JOURNAL_MODE=AUTO`.
4. Если не задавать `PRODUCT_DB_PATH`, приложение само выберет `<RAILWAY_VOLUME_MOUNT_PATH>/nimarita.db`.
5. Если не задавать `PRODUCT_BACKUP_DIR`, backup-ы будут складываться в `<RAILWAY_VOLUME_MOUNT_PATH>/backups`.
6. После деплоя проверь `/health` и `/api/v1/health/ready`.

### Что проверить после деплоя

1. `/start` открывает сообщение с кнопкой входа в Mini App.
2. В Mini App можно выбрать роль.
3. После создания пары доступны регулярные напоминания и раздел заботливых сообщений.
4. После рестарта сервиса пара, напоминания и история остаются на месте.

## 6. Закрытый rollout

```env
ACCESS_ALLOWLIST_ENABLED=true
ALLOWED_USER_IDS=123456789,987654321
```

## 7. Cleanup после миграции

Если эта версия накатывается поверх старого checkout, один раз выполни:

```bash
python scripts/cleanup_legacy_layout.py --apply
```

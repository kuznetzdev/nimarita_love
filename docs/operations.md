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
- `GET /health/live`
- `GET /health/ready`

`ready` проверяет доступность БД, состояние последнего аудита и heartbeat воркеров.

## 5. SQLite режим

Для высокой надёжности в single-instance режиме используются:

- `WAL`
- `synchronous=FULL`
- `busy_timeout`
- авто-checkpoint
- quick-check по расписанию
- hot-backup snapshots
- graceful shutdown checkpoint

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

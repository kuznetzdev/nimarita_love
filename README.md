# Nimarita

Nimarita is a Telegram bot plus Telegram Mini App for confirmed romantic pairs 1:1.

Current product boundaries:

- one user can have at most one active pair;
- reminders exist only inside the active pair;
- care messages exist only inside the active pair;
- Telegram bot is both the onboarding and delivery channel;
- the Mini App is the main user workspace;
- SQLite-backed single-instance deployment is the intended production model.

## Stack

- Python 3.11+
- `aiogram` for the Telegram bot runtime
- `aiohttp` for the Mini App backend
- SQLite for persistence
- Railway-compatible deployment with a persistent volume

## Repository structure

```text
.
|-- nimarita/
|   |-- app.py
|   |-- runner.py
|   |-- config.py
|   |-- logging.py
|   |-- catalog/
|   |-- domain/
|   |-- infra/
|   |-- repositories/
|   |-- services/
|   |-- telegram/
|   |-- web/
|   |   `-- static/index.html
|   `-- workers/
|-- docs/
|   |-- NIMARITA_PRODUCTION_DOCS_INDEX.md
|   |-- NIMARITA_DEVELOPER_HANDOFF.md
|   |-- NIMARITA_PRODUCTION_ARCHITECTURE.md
|   |-- NIMARITA_PRODUCTION_DATA_MODEL.md
|   |-- NIMARITA_PRODUCTION_API_CONTRACTS.md
|   |-- NIMARITA_PRODUCTION_FRONTEND_GUIDE.md
|   |-- NIMARITA_PRODUCTION_USER_FLOWS.md
|   |-- NIMARITA_PRODUCTION_OPERATIONS.md
|   `-- NIMARITA_OPERATIONS_RUNBOOK.md
|-- scripts/
|   `-- cleanup_legacy_layout.py
|-- tests/
|-- .env.example
|-- main.py
`-- requirements.txt
```

## Quick start

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python main.py
```

Alternative entry point:

```powershell
python -m nimarita
```

## Minimum required configuration

Required:

- `BOT_TOKEN`
- `BOT_USERNAME`

Required for Mini App usage:

- `WEBAPP_PUBLIC_URL` with HTTPS

Recommended for production:

- `APP_SESSION_SECRET`
- persistent volume via Railway

## Commands

Current Telegram command surface:

- `/start`
- `/open`
- `/pair`
- `/profile`
- `/status`
- `/remind`
- `/care`
- `/help`
- `/unpair`

## Access modes

Open mode:

```env
ACCESS_ALLOWLIST_ENABLED=false
```

Closed beta:

```env
ACCESS_ALLOWLIST_ENABLED=true
ALLOWED_USER_IDS=123456789,987654321
```

## Production notes

Current production assumptions:

- one Railway service;
- one persistent volume;
- one running instance;
- one SQLite database file on that volume.

Do not run multiple replicas against the same SQLite database.

## Documentation

Canonical documentation entry point:

- `docs/NIMARITA_PRODUCTION_DOCS_INDEX.md`

Recommended first read for a new developer:

- `docs/NIMARITA_DEVELOPER_HANDOFF.md`

## Tests

Run the current test suite with:

```powershell
python -m unittest discover -v
```

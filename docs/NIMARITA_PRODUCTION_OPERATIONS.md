# Nimarita - Production Operations

## 1. Deployment model

The current production deployment is intentionally simple:

- one Railway service;
- one persistent Railway volume;
- one application instance;
- one SQLite database file on the volume.

This is not a horizontally scaled service.

## 2. Current runtime startup

Current startup sequence:

1. load settings;
2. connect SQLite;
3. build repositories and services;
4. call startup reconciliation;
5. run startup DB audit;
6. log deployment warnings;
7. optionally create startup backup;
8. start workers;
9. start web server;
10. sync Telegram menu buttons;
11. start bot polling.

Operational consequence: readiness should be treated as the deployment gate, not liveness alone.

## 3. Required environment

### Mandatory

- `BOT_TOKEN`
- `BOT_USERNAME`

### Operationally required for production

- `WEBAPP_PUBLIC_URL`
- `APP_SESSION_SECRET`
- persistent volume via `RAILWAY_VOLUME_MOUNT_PATH`

### Common product/runtime settings

- `WEBAPP_ENABLED`
- `WEBAPP_LISTEN_HOST`
- `WEBAPP_LISTEN_PORT`
- `DEFAULT_TIMEZONE`
- `PAIR_INVITE_TTL_MINUTES`
- `MINI_APP_SHORT_NAME`
- `MINI_APP_TITLE`

### Access policy

- `ACCESS_ALLOWLIST_ENABLED`
- `ALLOWED_USER_IDS`

### SQLite maintenance and durability

- `PRODUCT_DB_PATH`
- `PRODUCT_BACKUP_DIR`
- `SQLITE_JOURNAL_MODE`
- `SQLITE_SYNCHRONOUS`
- `SQLITE_BUSY_TIMEOUT_MS`
- `SQLITE_WAL_AUTOCHECKPOINT_PAGES`
- `SQLITE_JOURNAL_SIZE_LIMIT_BYTES`
- `SQLITE_CHECKPOINT_MODE`
- `SQLITE_CHECKPOINT_INTERVAL_SECONDS`
- `SQLITE_QUICK_CHECK_ON_STARTUP`
- `SQLITE_QUICK_CHECK_INTERVAL_SECONDS`
- `SQLITE_FAIL_FAST_ON_INTEGRITY_ERROR`

### Backups

- `BACKUP_ENABLED`
- `BACKUP_ON_STARTUP`
- `BACKUP_ON_SHUTDOWN`
- `BACKUP_INTERVAL_SECONDS`
- `BACKUP_RETENTION`

### Workers

- `REMINDER_WORKER_POLL_SECONDS`
- `REMINDER_WORKER_BATCH_SIZE`
- `REMINDER_WORKER_CONCURRENCY`
- `REMINDER_MAX_RETRIES`
- `REMINDER_RETRY_BASE_SECONDS`
- `CARE_WORKER_POLL_SECONDS`
- `CARE_WORKER_BATCH_SIZE`
- `CARE_WORKER_CONCURRENCY`
- `CARE_MAX_RETRIES`
- `CARE_RETRY_BASE_SECONDS`
- `CLEANUP_WORKER_POLL_SECONDS`
- `CLEANUP_WORKER_BATCH_SIZE`
- `PROCESSING_STALE_SECONDS`
- `WORKER_HEARTBEAT_STALE_SECONDS`

## 4. Storage model

If `PRODUCT_DB_PATH` and `PRODUCT_BACKUP_DIR` are unset and a Railway volume is mounted, the application defaults to:

- `<RAILWAY_VOLUME_MOUNT_PATH>/nimarita.db`
- `<RAILWAY_VOLUME_MOUNT_PATH>/backups`

This is the intended production configuration.

## 5. SQLite journal mode behavior

Current `SQLITE_JOURNAL_MODE=AUTO` resolution:

- if Railway volume is present and DB path is inside the data root, runtime resolves to `DELETE`;
- otherwise runtime resolves to `WAL`.

Why this matters:

- Railway volume plus WAL requires `-wal` and `-shm` sidecar handling;
- the current app still assumes a single instance either way;
- readiness reports the resolved mode and warnings.

## 6. Health and readiness

### Liveness

Use:

- `/health`
- `/health/live`
- `/api/v1/health`
- `/api/v1/health/live`

Liveness only tells you the process is up.

### Readiness

Use:

- `/health/ready`
- `/api/v1/health/ready`

Readiness contains three categories of checks:

- `checks.db`
- `checks.workers`
- `checks.deployment`

### `checks.db`

Current readiness includes:

- `ok`
- `audit_ok`
- `last_audit`
- `last_checkpoint`
- `last_backup`
- `last_backup_age_seconds`

### `checks.workers`

Current readiness reports each worker heartbeat, last iteration, and last error.

### `checks.deployment`

Current readiness exposes:

- `database_path`
- `backup_directory`
- `sqlite_journal_mode`
- `sqlite_synchronous`
- `warnings`

This is important because deployment risks are surfaced here even when the app still starts.

## 7. Deployment warnings currently emitted

The system logs and audits warnings for:

- multi-replica use of SQLite against the same DB;
- database path outside the Railway volume;
- backup directory outside the Railway volume;
- WAL on Railway volume with sidecar file implications;
- unsafe `journal_mode=OFF`;
- unsafe `synchronous=OFF`;
- `APP_SESSION_SECRET` equal to `BOT_TOKEN`.

These warnings do not automatically fail startup.

## 8. Backup and maintenance model

Current operations support:

- startup backup;
- periodic backup;
- periodic checkpoint;
- periodic quick check;
- graceful shutdown checkpoint;
- optional shutdown backup.

Important caveats:

- restore validation is manual;
- backup naming can collide if multiple backups are created within the same second;
- backups are not a substitute for correct volume mounting.

## 9. Railway deployment contract

### Required rules

1. attach a persistent volume;
2. keep replica count at exactly `1`;
3. expose a valid HTTPS public URL;
4. set BotFather Mini App URL to that same origin;
5. do not put the DB on ephemeral container storage.

### `railway.toml`

Current contract:

- builder: `railpack`
- start command: `python main.py`
- healthcheck path: `/health/ready`

## 10. Operational risks

These are current real risks in the deployed design.

- Single-instance behavior is an operational rule, not enforced through distributed coordination.
- SQLite remains the primary state store and write bottleneck.
- Session hardening depends on explicitly setting `APP_SESSION_SECRET`.
- The service warns about unsafe storage layout but still starts.
- There is no formal migration framework yet.

## 11. What must not be changed casually

- single-instance deployment model;
- DB path and backup path layout;
- readiness contract fields;
- startup reconciliation behavior;
- worker heartbeat reporting;
- Telegram bot/web server co-location in one runtime.

## 12. Safe continuation path

Recommended operational improvements:

1. automate restore validation;
2. add a schema migration framework;
3. add structured metrics export;
4. move from warning-only to fail-fast for selected unsafe deployment states;
5. introduce stronger secret validation at startup.

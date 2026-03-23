# Nimarita - Operations Runbook

## 1. Purpose

This runbook is for deploying, verifying, and supporting the current Nimarita production service.

It is written for the actual current architecture:

- one async Python service;
- one SQLite database;
- one Railway volume;
- one running instance.

## 2. Pre-deploy checklist

Before a production deploy, verify:

- `BOT_TOKEN` is valid;
- `BOT_USERNAME` matches the actual bot;
- `WEBAPP_PUBLIC_URL` is HTTPS and points to the current deployment;
- BotFather Mini App URL matches the same origin;
- `APP_SESSION_SECRET` is set and is not equal to `BOT_TOKEN`;
- Railway volume is attached;
- target replica count is `1`.

## 3. Deployment steps

1. Deploy the current branch to Railway.
2. Wait for process startup.
3. Check `/health/live`.
4. Check `/health/ready`.
5. Inspect readiness `checks.deployment.warnings`.
6. Inspect startup logs for database audit and worker startup.

Do not mark the deploy healthy from liveness alone.

## 4. Post-deploy smoke test

Run this exact smoke sequence:

1. Open `/health/ready` and confirm `ok = true`.
2. Send `/start` to the bot from account A.
3. Open the Mini App from account A.
4. Save a profile role.
5. Create an invite from account A.
6. Send `/start` to the bot from account B.
7. Open the invite from account B and accept it.
8. Confirm both users now have active pair state.
9. Create one reminder.
10. Send one care template.
11. Send one custom care message.
12. Confirm reminder and care delivery in Telegram.

## 5. Readiness interpretation

### Healthy

Good readiness means:

- `checks.db.ok = true`
- `checks.db.audit_ok = true`
- worker entries are fresh and `ok = true`
- deployment warnings are understood and acceptable

### Degraded

If readiness is `503`, check in this order:

1. DB audit result
2. worker heartbeat freshness
3. startup reconciliation errors
4. storage path warnings
5. recent bot/API exceptions in logs

## 6. Common incidents

### Incident: Mini App does not bootstrap

Check:

1. Mini App is opened from Telegram, not from a standalone browser.
2. `POST /api/v1/auth` succeeds.
3. `WEBAPP_PUBLIC_URL` matches the deployment.
4. session token is returned.
5. session-authenticated `GET /api/v1/state` succeeds.

Likely causes:

- invalid or stale Telegram `initData`;
- broken public origin configuration;
- session auth failure;
- access allowlist rejection.

### Incident: Invite cannot be accepted

Check:

1. invite is still pending;
2. invite is for the current user;
3. neither user already has active pair;
4. both users completed `/start`;
5. no stale state is being inferred in the UI.

Likely causes:

- one side never started the bot;
- existing active pair conflict;
- invite expired;
- wrong user attempts acceptance.

### Incident: Reminder was created but not delivered

Check:

1. reminder worker heartbeat;
2. occurrence status and retry counters;
3. recipient `private_chat_id`;
4. pair still active;
5. Telegram send errors;
6. final failure state.

Likely causes:

- recipient never started the bot;
- worker stalled or crashed;
- occurrence reached max retries;
- pair closed before delivery.

### Incident: Care message was sent but reply did not reach sender

Check:

1. dispatch status;
2. whether the dispatch already has a response;
3. sender notification logs;
4. pair still active;
5. worker/notifier health.

Likely causes:

- duplicate response attempt;
- send error on response notification;
- stale session or incorrect user ownership;
- pair closed around the same time.

### Incident: Data appears reset after restart

Check:

1. readiness `checks.deployment.database_path`;
2. readiness `checks.deployment.backup_directory`;
3. Railway volume mount;
4. whether the DB path is outside the volume.

Most likely cause:

- DB was written to ephemeral container storage instead of the persistent volume.

## 7. Backup and restore handling

### What the app currently does

- SQLite backup API based backups;
- startup backup if enabled;
- periodic backup if enabled;
- optional shutdown backup;
- retention cleanup.

### What operators still must do manually

- verify backup files exist and rotate;
- test restores outside production;
- confirm restored DB passes quick check and can boot the app.

### Manual restore guidance

1. stop the service;
2. copy the selected backup over the target DB path;
3. start the service with one instance only;
4. verify readiness;
5. verify bot and Mini App smoke flows.

There is no automated restore validation in the current codebase.

## 8. Safe operational changes

Safe changes:

- tune worker intervals conservatively;
- adjust allowlist users;
- adjust backup retention;
- change `WEBAPP_PUBLIC_URL` together with BotFather configuration.

Unsafe changes that require extra care:

- enabling multiple replicas;
- moving DB path off the persistent volume;
- changing journal mode without understanding Railway storage behavior;
- disabling backups in production;
- changing session secret handling.

## 9. Debugging order of operations

When the product is broken, debug in this order:

1. readiness
2. deployment warnings
3. recent logs
4. current DB row state for the affected entity
5. Telegram delivery path
6. frontend bootstrap state

This order is usually faster than starting from the UI.

## 10. Operational assumptions that must stay explicit

- one process is the intended production mode;
- SQLite is the source of truth;
- reminders and care rely on background workers in the same runtime;
- Telegram bot private chat is required for outbound delivery;
- readiness is the primary health contract.

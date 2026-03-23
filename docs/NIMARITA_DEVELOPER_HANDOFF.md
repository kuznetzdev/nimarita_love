# Nimarita - Developer Handoff

## 1. What this project is

Nimarita is a Telegram bot plus Telegram Mini App for confirmed romantic pairs 1:1.

The product currently does three things well:

- pairs two users through invite confirmation;
- lets one partner schedule reminders for the other;
- lets partners exchange warm predefined or custom care messages.

The backend is the source of truth. The Mini App is a client, not an authority.

## 2. What a new developer should understand first

Before changing anything, understand these facts:

1. The active pair invariant defines the whole application.
2. `/start` is part of the domain lifecycle, not only onboarding copy.
3. Reminders are modeled as rule plus occurrence, not as one table.
4. Care messages are modeled as queued dispatches with persisted payload snapshots.
5. Telegram bot delivery is not optional; it is part of the core product behavior.
6. SQLite is a deliberate current production choice and implies one instance.

## 3. Hot files to read first

Read these files in order:

1. `nimarita/app.py`
2. `nimarita/services/pairing.py`
3. `nimarita/services/reminders.py`
4. `nimarita/services/care.py`
5. `nimarita/services/system.py`
6. `nimarita/repositories/pairing.py`
7. `nimarita/repositories/reminders.py`
8. `nimarita/web/server.py`
9. `nimarita/web/auth.py`
10. `nimarita/telegram/router.py`
11. `nimarita/web/static/index.html`
12. `nimarita/infra/sqlite.py`

## 4. Core invariants that must not be broken

### Pair lifecycle

- one user can have only one active pair;
- canonical pair ordering must remain stable;
- both users must complete `/start` before pair confirmation;
- unpair must cascade into reminder/care cleanup.

### Reminder lifecycle

- reminders exist only for active pairs;
- creator and recipient are always pair members;
- rule and occurrence must remain separate;
- recipient actions operate on occurrences;
- duplicate-submit reuse must remain compatible with UI busy-state behavior.

### Care lifecycle

- care exists only for active pairs;
- template filtering depends on sender/recipient roles;
- dispatch snapshots must preserve historical content;
- recipient can respond only once;
- duplicate and rate-limit protections are part of expected behavior.

### Web and auth

- `state.mode` is canonical and must stay stable;
- all protected endpoints must keep session verification plus allowlist recheck;
- Mini App bootstrap depends on `/api/v1/auth` response shape.

## 5. Pair lifecycle in one paragraph

User A starts the bot, creates an invite, shares the link, user B opens the invite and must also complete `/start`, the backend confirms the pair only when both sides have bot onboarding and no active-pair conflicts, then reminders and care become available; unpair closes the pair and cancels open pair-scoped work.

## 6. How reminders work in one paragraph

The user creates a reminder in local time, the backend stores a rule plus the first occurrence in UTC, the reminder worker claims due scheduled occurrences and sends them through Telegram, the recipient can mark the reminder done or snooze it, recurring rules schedule the next occurrence after successful delivery, and the creator can update or cancel the reminder while the pair remains active.

## 7. How care messages work in one paragraph

The sender chooses a seeded template or creates a custom message, the backend validates active pair and recipient eligibility, queues a care dispatch, the care worker sends the message to Telegram, the recipient can send a quick reply or custom reply once, and the sender receives a response notification while the dispatch history preserves the exact sent payload.

## 8. How Mini App flow works in one paragraph

The Mini App opens only from Telegram context, sends verified `init_data` to `/api/v1/auth`, receives a bearer session and canonical dashboard payload, renders one of the backend-defined modes, performs mutations through protected API endpoints, and periodically refreshes state while visible.

## 9. How Telegram bot flow works in one paragraph

The bot registers users, exposes invite/profile/unpair shortcuts, renders dashboard panels, and delivers reminders, care messages, confirmations, and response notifications; the bot is both a control surface and the actual outbound delivery channel.

## 10. Current risks a new developer must keep in mind

- Single-instance operation is mandatory with current SQLite design.
- Startup schema evolution is not a formal migration system.
- `start_param` in web auth is not server-signed yet.
- Reminder terminal delivery transitions are not fully race-hardened.
- Backup restore verification is still manual.
- Session hardening depends on explicitly setting `APP_SESSION_SECRET`.

## 11. Safe areas for continued development

These are relatively safe next tasks:

- add focused tests around existing service behavior;
- modularize the frontend without changing API contracts;
- improve observability around workers and readiness;
- add formal migration tooling;
- strengthen auth/start-param handling;
- harden reminder state transitions.

## 12. Changes that require extra caution

Treat these as high-risk:

- changing pair confirmation rules;
- changing reminder serialized field names;
- changing care response semantics;
- changing readiness payload structure;
- introducing multi-instance deployment;
- moving the DB off the persistent volume contract;
- combining frontend refactor with API contract changes.

## 13. Recommended first-week plan for a new owner

1. Read the production docs bundle in the documented order.
2. Run the current test suite.
3. Boot the app locally and execute the pair/reminder/care happy path.
4. Inspect readiness payload and logs during startup.
5. Read the current tests that cover the recent hardening cases.
6. Only then start changes in one domain at a time.

## 14. Handoff summary

If you remember only five things, remember these:

1. pair invariant first;
2. `/start` is part of business correctness;
3. reminder rule plus occurrence is non-negotiable;
4. Mini App contract stability matters as much as backend logic;
5. production is one instance plus one persistent SQLite database.

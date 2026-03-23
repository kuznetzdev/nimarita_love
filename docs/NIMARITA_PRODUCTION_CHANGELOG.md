# Nimarita - Production Changelog

> This changelog documents implemented changes confirmed in the current repository snapshot.  
> It is not a literal commit-by-commit git history.

## 1. Product baseline now present in code

The current codebase implements Nimarita as a strict pair-first Telegram product:

- one active pair per user;
- Telegram bot as onboarding and delivery channel;
- Telegram Mini App as the main workspace;
- reminders and care messages locked to the active pair.

## 2. Pair lifecycle implementation

Implemented and present:

- hashed invite tokens;
- pending invite TTL;
- invite preview flow;
- active-pair uniqueness enforced both in service logic and in DB triggers;
- invite accept/reject/cancel flows;
- unpair flow with downstream cleanup.

Confirmed current behavior:

- pair confirmation requires both users to complete `/start`;
- web-only invite preview does not bind the invite before bot onboarding;
- conflicting pending invites are expired on successful pair confirmation.

## 3. Reminder engine implementation

Implemented and present:

- reminder rules plus reminder occurrences;
- one-time and recurring reminders;
- recurrence kinds `one_time`, `daily`, `weekdays`, `weekly`, `interval`;
- worker-based reminder delivery;
- retry and stale-processing recovery;
- recipient actions `done` and `snooze`;
- creator update and cancel flows.

Confirmed current hardening:

- duplicate reminder submit reuse within a recent window for equivalent active reminders;
- creator timezone is persisted and reused for scheduling semantics.

## 4. Care domain implementation

Implemented and present:

- seeded care catalog with DB upsert;
- role-aware template filtering;
- queued care dispatches with retry metadata;
- care history;
- quick replies;
- custom care sends;
- custom replies;
- sender notifications after response.

Confirmed current hardening:

- rate limits per minute and per hour;
- duplicate template prevention window;
- duplicate custom-care submit reuse within a recent window.

## 5. Telegram bot implementation

Implemented and present:

- command surface for start, pair, profile, status, remind, care, help, unpair;
- dashboard upsert via tracked panel records;
- reminder delivery cards with inline actions;
- care delivery cards with quick replies and pagination;
- ephemeral message cleanup flow.

Confirmed current UX improvement:

- outgoing invite dashboard state has explicit cancel support in Telegram flow.

## 6. Mini App backend implementation

Implemented and present:

- Telegram `initData` verification;
- stateless bearer session tokens;
- protected API surface for pair/reminder/care/profile/state;
- dashboard payload aggregation from backend state;
- liveness and readiness endpoints.

Confirmed current hardening:

- strict `initData` parsing with duplicate-key rejection and bounds checks;
- protected endpoints recheck allowlist and user existence on every session-authenticated request;
- JSON body parsing rejects malformed and non-object JSON bodies.

## 7. Mini App frontend implementation

Implemented and present:

- single-file Mini App shell;
- bootstrap auth flow;
- active pair workspace tabs;
- reminder list plus collapsible composer;
- care template browser, custom care composer, history;
- auto-refresh while visible;
- busy-state button wrappers for major mutations.

Confirmed current UX direction:

- pair workspace keeps primary actions prominent and destructive actions secondary;
- sticky tab navigation is used for long mobile screens;
- outgoing invite state keeps the main invite action visible and secondary actions quieter.

## 8. Reliability and operations implementation

Implemented and present:

- startup reconciliation for stale work;
- startup DB audit;
- request-id and logging support;
- worker heartbeat registry;
- readiness payload with DB, workers, and deployment sections;
- deployment warning logging;
- SQLite checkpoint, quick-check, and backup support;
- graceful shutdown maintenance.

Confirmed current visibility improvement:

- readiness now exposes deployment-specific fields and warnings, not only process health.

## 9. Current known limits still true

These are not missing docs. They are current system realities.

- single-instance SQLite remains the intended production mode;
- schema evolution is additive bootstrap logic, not a formal migration framework;
- restore validation is still manual;
- `APP_SESSION_SECRET` can still fall back to `BOT_TOKEN` if left unset;
- auth still trusts request `start_param` instead of a server-signed value;
- reminder delivery terminal transitions are not yet fully race-hardened.

## 10. Supporting tests currently present

The current repository includes tests that verify recent hardening behavior, including:

- strict web auth validation;
- web-only invite preview not binding before `/start`;
- reminder duplicate-submit reuse;
- custom care duplicate-submit reuse;
- session allowlist recheck on authenticated web requests;
- readiness deployment checks exposure.

# Nimarita - Production Documentation Index

This directory is the canonical documentation bundle for the current repository snapshot.

If a production document here disagrees with older `docs/architecture.md` or `docs/operations.md`, treat the production bundle as authoritative.

## Canonical files

- `NIMARITA_PRODUCTION_ARCHITECTURE.md` - system architecture, boundaries, invariants, lifecycle design, risks
- `NIMARITA_PRODUCTION_DATA_MODEL.md` - schema, enums, table semantics, persistence invariants
- `NIMARITA_PRODUCTION_API_CONTRACTS.md` - Mini App auth/session model and HTTP contracts
- `NIMARITA_PRODUCTION_FRONTEND_GUIDE.md` - Mini App structure, UI rules, interaction constraints
- `NIMARITA_PRODUCTION_USER_FLOWS.md` - Telegram bot flow, Mini App flow, pair/reminder/care lifecycle
- `NIMARITA_PRODUCTION_OPERATIONS.md` - deployment model, readiness, durability, operational assumptions
- `NIMARITA_OPERATIONS_RUNBOOK.md` - step-by-step deploy/support/runbook procedures
- `NIMARITA_PRODUCTION_CHANGELOG.md` - implemented changes confirmed in the current codebase
- `NIMARITA_DEVELOPER_HANDOFF.md` - what a new developer must understand first, what not to break, where to continue

## Recommended reading order for a new developer

1. `NIMARITA_DEVELOPER_HANDOFF.md`
2. `NIMARITA_PRODUCTION_ARCHITECTURE.md`
3. `NIMARITA_PRODUCTION_DATA_MODEL.md`
4. `NIMARITA_PRODUCTION_API_CONTRACTS.md`
5. `NIMARITA_PRODUCTION_USER_FLOWS.md`
6. `NIMARITA_PRODUCTION_FRONTEND_GUIDE.md`
7. `NIMARITA_PRODUCTION_OPERATIONS.md`
8. `NIMARITA_OPERATIONS_RUNBOOK.md`
9. `NIMARITA_PRODUCTION_CHANGELOG.md`

## Reading paths by role

### New backend developer

Read:

1. handoff
2. architecture
3. data model
4. API contracts
5. user flows

### Frontend or Mini App developer

Read:

1. handoff
2. frontend guide
3. API contracts
4. user flows

### Operator / on-call engineer

Read:

1. handoff
2. operations
3. runbook
4. changelog

## Short index by question

### What is this app

- architecture
- handoff

### How does pair lifecycle work

- architecture
- user flows
- data model

### How do reminders work

- architecture
- data model
- API contracts
- user flows

### How do care messages work

- architecture
- data model
- API contracts
- user flows

### How does Mini App flow work

- frontend guide
- API contracts
- user flows

### How does Telegram bot flow work

- architecture
- user flows
- runbook

### How to deploy and support it

- operations
- runbook

### What are the current risks

- architecture
- operations
- handoff

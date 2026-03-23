# Nimarita - Technical Documentation Entry Point

This file is the umbrella entry point for the current technical documentation set.

The authoritative current-state docs are the production bundle in this directory. Older parallel docs should not be treated as separate sources of truth.

## Read this first

- `NIMARITA_PRODUCTION_DOCS_INDEX.md`
- `NIMARITA_DEVELOPER_HANDOFF.md`

## Canonical current-state documents

- `NIMARITA_PRODUCTION_ARCHITECTURE.md`
- `NIMARITA_PRODUCTION_DATA_MODEL.md`
- `NIMARITA_PRODUCTION_API_CONTRACTS.md`
- `NIMARITA_PRODUCTION_FRONTEND_GUIDE.md`
- `NIMARITA_PRODUCTION_USER_FLOWS.md`
- `NIMARITA_PRODUCTION_OPERATIONS.md`
- `NIMARITA_OPERATIONS_RUNBOOK.md`
- `NIMARITA_PRODUCTION_CHANGELOG.md`

## What this project is

Nimarita is a Telegram bot plus Telegram Mini App for confirmed romantic pairs 1:1.

The current repository implements:

- strict active-pair lifecycle;
- pair-scoped reminders;
- pair-scoped care messages;
- Telegram bot delivery;
- Mini App as the primary user workspace;
- single-instance SQLite-backed production deployment.

## What to use this file for

Use this file only as a navigation entry point.

Do not duplicate architecture, API, or operations details here if they already exist in the production bundle. Update the canonical production document instead.

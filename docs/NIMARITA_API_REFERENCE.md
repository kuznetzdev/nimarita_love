# Nimarita - API Reference Entry Point

This file is kept as a compatibility entry point for readers looking for the API reference.

The authoritative API documentation is:

- `NIMARITA_PRODUCTION_API_CONTRACTS.md`

## Scope

The current API is private and intended for the bundled Telegram Mini App.

It covers:

- Telegram `initData` bootstrap auth;
- bearer session usage;
- dashboard state;
- pair lifecycle endpoints;
- reminder endpoints;
- care endpoints;
- liveness and readiness endpoints.

## Important contract reminder

Do not maintain a second API description in this file.

If request or response shapes change, update:

- `NIMARITA_PRODUCTION_API_CONTRACTS.md`

and treat that document as the single source of truth.

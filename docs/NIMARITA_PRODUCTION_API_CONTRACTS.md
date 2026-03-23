# Nimarita - Production API Contracts

## 1. API scope

This HTTP API is private and intended for the bundled Telegram Mini App.

The current contract assumptions are:

- all protected endpoints require a valid app session;
- the session is issued only after verified Telegram `initData`;
- the backend is the source of truth for dashboard mode and pair-scoped state;
- there is no generated OpenAPI spec in the repository.

## 2. Authentication and session model

### `POST /api/v1/auth`

Bootstrap endpoint for the Mini App.

Request body:

```json
{
  "init_data": "<Telegram.WebApp.initData>",
  "start_param": "invite_xxx or empty string"
}
```

Current behavior:

- verifies Telegram `initData` signature and freshness;
- touches or creates the user with `started_bot = false` if this is a web-only visit;
- optionally previews an invite if `start_param` starts with `invite_`;
- returns a short-lived bearer session token;
- returns full dashboard payload when possible.

Representative response:

```json
{
  "ok": true,
  "session_token": "<bearer>",
  "user": { "...": "..." },
  "state": { "...": "..." },
  "reminders": [],
  "care_templates": [],
  "care_history": [],
  "start_param": "invite_xxx",
  "invite_preview": null,
  "invite_preview_error": null
}
```

Important notes:

- `start_param` is echoed back;
- `invite_preview` is informational and may be absent;
- web-only preview does not bind the invite until the user has started the bot and has a private chat.

### Bearer session

Protected endpoints require:

```http
Authorization: Bearer <session_token>
```

Current session behavior:

- stateless HMAC-signed payload with subject and expiry;
- invalid, expired, malformed, or missing token returns `401`;
- every protected request rechecks allowlist and user existence.

## 3. Error model

Current API error mapping:

- `401` for auth/session failures;
- `403` for access policy denial;
- `400` for validation errors and malformed JSON;
- `404` for not found;
- `409` for conflicts;
- `500` for unexpected server errors.

The server rejects:

- malformed JSON;
- non-object JSON bodies;
- invalid enum values;
- invalid path ids.

## 4. Health endpoints

### Live

- `GET /health`
- `GET /health/live`
- `GET /api/v1/health`
- `GET /api/v1/health/live`

Representative response:

```json
{
  "ok": true,
  "service": "nimarita",
  "started_at": "2026-03-23T12:00:00+00:00"
}
```

### Ready

- `GET /health/ready`
- `GET /api/v1/health/ready`

Representative shape:

```json
{
  "ok": true,
  "service": "nimarita",
  "started_at": "2026-03-23T12:00:00+00:00",
  "uptime_seconds": 1234.5,
  "checks": {
    "db": {
      "ok": true,
      "audit_ok": true
    },
    "workers": [],
    "deployment": {
      "database_path": "...",
      "backup_directory": "...",
      "sqlite_journal_mode": "DELETE",
      "sqlite_synchronous": "FULL",
      "warnings": []
    }
  }
}
```

Readiness returns `503` when the service is degraded.

## 5. Shared serialized objects

### Serialized user

Representative shape:

```json
{
  "id": 1,
  "telegram_user_id": 123456789,
  "username": "alice",
  "first_name": "Alice",
  "last_name": null,
  "display_name": "Alice",
  "timezone": "Europe/Moscow",
  "relationship_role": "woman",
  "relationship_role_label": "Woman",
  "private_chat_id": 123456789,
  "started_bot": true
}
```

### Serialized state

`state.mode` is canonical and must drive UI rendering.

Representative shape:

```json
{
  "mode": "no_pair",
  "user": { "...": "..." },
  "partner": null,
  "active_pair": null,
  "outgoing_invite": null,
  "incoming_invite": null
}
```

When active:

```json
{
  "mode": "active",
  "user": { "...": "..." },
  "partner": { "...": "..." },
  "active_pair": {
    "id": 10,
    "status": "active",
    "confirmed_at": "2026-03-23T10:00:00+00:00"
  },
  "outgoing_invite": null,
  "incoming_invite": null
}
```

## 6. State and profile endpoints

### `GET /api/v1/state`

Returns the full dashboard payload:

```json
{
  "ok": true,
  "state": { "...": "..." },
  "reminders": [],
  "care_templates": [],
  "care_history": []
}
```

### `POST /api/v1/profile`

Request body:

```json
{
  "relationship_role": "woman"
}
```

Allowed values:

- `woman`
- `man`
- `unspecified`

Response:

```json
{
  "ok": true,
  "user": { "...": "..." },
  "state": { "...": "..." },
  "reminders": [],
  "care_templates": [],
  "care_history": []
}
```

## 7. Pairing endpoints

### `POST /api/v1/pairs/invite`

No request body.

Response status: `201`

```json
{
  "ok": true,
  "invite": {
    "id": 123,
    "expires_at": "2026-03-26T12:00:00+00:00",
    "bot_start_link": "https://t.me/...",
    "mini_app_link": "https://t.me/...",
    "token": "raw_token_here"
  },
  "state": { "...": "..." },
  "reminders": [],
  "care_templates": [],
  "care_history": []
}
```

### `POST /api/v1/pairs/invite/cancel`

No request body.

Response:

```json
{
  "ok": true,
  "state": { "...": "..." },
  "reminders": [],
  "care_templates": [],
  "care_history": []
}
```

### `POST /api/v1/pairs/accept`

Supported request bodies:

```json
{ "token": "raw_invite_token" }
```

or

```json
{ "invite_id": 123 }
```

Response:

```json
{
  "ok": true,
  "state": { "...": "..." },
  "reminders": [],
  "care_templates": [],
  "care_history": []
}
```

### `POST /api/v1/pairs/reject`

Supported request bodies:

```json
{ "token": "raw_invite_token" }
```

or

```json
{ "invite_id": 123 }
```

Response shape is the same as pair accept.

### `POST /api/v1/pairs/unpair`

No request body.

Response:

```json
{
  "ok": true,
  "state": { "...": "..." },
  "reminders": [],
  "care_templates": [],
  "care_history": []
}
```

## 8. Reminder endpoints

### Reminder object

Representative shape:

```json
{
  "rule_id": 55,
  "occurrence_id": 144,
  "kind": "weekly",
  "kind_label": "Weekly",
  "text": "Buy flowers",
  "status": "scheduled",
  "rule_status": "active",
  "handled_action": null,
  "scheduled_at_utc": "2026-03-25T18:00:00+00:00",
  "next_attempt_at_utc": "2026-03-25T18:00:00+00:00",
  "origin_scheduled_at_utc": "2026-03-25T18:00:00+00:00",
  "creator_timezone": "Europe/Moscow",
  "recurrence_every": 1,
  "recurrence_unit": null,
  "cancelled_at": null,
  "creator": { "...": "..." },
  "recipient": { "...": "..." },
  "delivery_attempts_count": 0,
  "last_error": null,
  "telegram_message_id": null
}
```

### `GET /api/v1/reminders`

Response:

```json
{
  "ok": true,
  "reminders": []
}
```

### `POST /api/v1/reminders`

Request body:

```json
{
  "text": "Buy flowers",
  "scheduled_for_local": "2026-03-25T21:00",
  "timezone": "Europe/Moscow",
  "kind": "interval",
  "recurrence_every": 2,
  "recurrence_unit": "day"
}
```

Important contract details:

- the field is `scheduled_for_local`, not `scheduled_at`;
- `timezone` is required logically even though the server can fall back to `DEFAULT_TIMEZONE`;
- `recurrence_every` and `recurrence_unit` matter for `interval`;
- current kinds are `one_time`, `daily`, `weekdays`, `weekly`, `interval`.

Response status: `201`

```json
{
  "ok": true,
  "reminder": { "...": "..." },
  "reminders": []
}
```

### `POST /api/v1/reminders/{rule_id}`

Request body uses the same shape as reminder creation.

Response:

```json
{
  "ok": true,
  "reminder": { "...": "..." },
  "reminders": []
}
```

### `POST /api/v1/reminders/{rule_id}/cancel`

No request body.

Response:

```json
{
  "ok": true,
  "reminder": { "...": "..." },
  "reminders": []
}
```

## 9. Care endpoints

### Care template object

Representative shape:

```json
{
  "id": 1,
  "template_code": "support_gentle",
  "category": "support",
  "category_label": "Support",
  "title": "I am with you",
  "body": "You are not alone.",
  "emoji": "💌",
  "sender_role": "unspecified",
  "recipient_role": "woman",
  "recipient_hint": "For her",
  "tone_label": "Gentle",
  "sort_order": 10
}
```

### Care dispatch object

Representative shape:

```json
{
  "id": 91,
  "pair_id": 10,
  "template_code": "custom",
  "category": "custom",
  "category_label": "Custom",
  "title": "My message",
  "body": "I miss you",
  "emoji": "💌",
  "recipient_hint": "For partner",
  "tone_label": "Warm",
  "status": "sent",
  "telegram_message_id": 999,
  "response_code": null,
  "response_title": null,
  "response_body": null,
  "response_emoji": null,
  "response_clicked_at": null,
  "next_attempt_at_utc": null,
  "processing_started_at": null,
  "delivery_attempts_count": 1,
  "sent_at": "2026-03-23T12:00:00+00:00",
  "delivered_at": "2026-03-23T12:00:01+00:00",
  "last_error": null,
  "created_at": "2026-03-23T11:59:59+00:00",
  "updated_at": "2026-03-23T12:00:01+00:00",
  "sender": { "...": "..." },
  "recipient": { "...": "..." },
  "direction": "outbound",
  "quick_replies": []
}
```

### `GET /api/v1/care/templates`

Optional query parameter:

- `category`

Response:

```json
{
  "ok": true,
  "templates": []
}
```

### `GET /api/v1/care/history`

Response:

```json
{
  "ok": true,
  "history": []
}
```

### `POST /api/v1/care/send`

Request body:

```json
{
  "template_code": "support_gentle"
}
```

Response status: `202`

```json
{
  "ok": true,
  "dispatch": { "...": "..." },
  "history": []
}
```

### `POST /api/v1/care/respond`

Request body:

```json
{
  "dispatch_id": 91,
  "reply_code": "hug"
}
```

Response:

```json
{
  "ok": true,
  "dispatch": { "...": "..." },
  "history": []
}
```

### `POST /api/v1/care/send-custom`

Request body:

```json
{
  "title": "My message",
  "message": "I miss you",
  "emoji": "💌"
}
```

Important note: the server expects `message` as the body field, not `body`.

Response status: `202`

```json
{
  "ok": true,
  "dispatch": { "...": "..." },
  "history": []
}
```

### `POST /api/v1/care/respond-custom`

Request body:

```json
{
  "dispatch_id": 91,
  "title": "My reply",
  "message": "Thank you",
  "emoji": "💗"
}
```

Response:

```json
{
  "ok": true,
  "dispatch": { "...": "..." },
  "history": []
}
```

## 10. Compatibility rules for future development

These response shapes are already wired into the Mini App and should be treated as compatibility contracts.

Do not casually change:

- `state.mode`;
- invite response keys `bot_start_link` and `mini_app_link`;
- reminder input field `scheduled_for_local`;
- care response keys `dispatch` and `history`;
- reminder response keys `reminder` and `reminders`;
- session bootstrap field names.

## 11. Known contract risks

- `/api/v1/auth` still trusts the request `start_param` field instead of a server-signed value.
- Error granularity can still leak some entity existence differences.
- There is no machine-generated schema or contract test suite for all endpoints yet.

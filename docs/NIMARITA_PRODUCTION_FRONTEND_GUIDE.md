# Nimarita - Frontend and UX Guide

## 1. Frontend role

The Telegram Mini App is the main user workspace.

Its responsibilities are limited and explicit:

- bootstrap authenticated app access from Telegram;
- render canonical backend state;
- let the user perform pair, reminder, and care actions;
- stay readable inside Telegram mobile webview constraints.

The frontend must not become a second source of truth.

## 2. Current implementation shape

Frontend source:

- `nimarita/web/static/index.html`

Current implementation characteristics:

- single-file HTML/CSS/JS bundle;
- no framework runtime;
- manual state management;
- backend-driven rendering.

This is acceptable in the current snapshot and should be treated as a deliberate monolith, not as an accident.

## 3. Bootstrap flow

Current startup sequence:

1. apply theme tokens;
2. call `tg.ready()`;
3. call `tg.expand()`;
4. resolve `start_param` from Telegram context or URL;
5. `POST /api/v1/auth`;
6. store `session_token`;
7. call `applyPayload(...)`;
8. start periodic refresh every 30 seconds while visible.

Important contract details:

- `api()` uses bearer auth after bootstrap;
- requests use `cache: no-store`;
- requests use a 25-second timeout;
- auto-refresh also resumes on visibility return.

## 4. Canonical UI state

Current client state includes:

- `currentState`
- `inviteToken`
- `inviteId`
- `reminders`
- `careTemplates`
- `careHistory`
- active tab and expanded/collapsed section flags
- reminder edit mode and section expansion state

The UI must always be derived from backend payloads:

- `state`
- `reminders`
- `care_templates`
- `care_history`

## 5. Main rendered sections

The current markup contains these primary sections:

- profile role card
- `#no-pair`
- `#outgoing`
- `#incoming`
- `#active`
- sticky workspace tabs
- reminder form card
- reminders card
- care browser card
- custom care card
- care history card

Only one pair mode section should be prominent at a time.

## 6. Canonical modes

### `no_pair`

Main job:

- explain that the user is not paired;
- offer invite creation;
- keep role/profile editing reachable.

### `outgoing_invite`

Main job:

- show that the user is waiting for confirmation;
- let the user copy/share the invite link;
- let the user cancel the outgoing invite.

### `incoming_invite`

Main job:

- show who invited the user;
- offer accept or reject;
- avoid unrelated workspaces until the pair is confirmed.

### `active`

Main job:

- expose pair workspace;
- expose reminders;
- expose care messages;
- keep destructive pair actions secondary.

## 7. Navigation model

Current active-pair navigation uses three tabs:

- `Pair`
- `Reminders`
- `Messages`

Important UX behavior:

- tabs are sticky on scroll;
- pair actions remain fast to reach on long pages;
- primary CTAs are shown before destructive actions;
- secondary maintenance actions are visually quieter.

## 8. Reminder UX

Current reminder experience is list-first.

Important behaviors:

- reminder list is visible as a primary workspace section;
- the composer is collapsible;
- edit mode keeps the composer open;
- restore mode keeps the composer open for cancelled reminders;
- reminder kind controls are conditional;
- schedule entry is split into separate date and time inputs;
- quick presets can prefill the next schedule without opening the native picker;
- long lists can expand with "show more";
- history/details are grouped under reminder cards.

Supported kinds exposed by the current UI:

- one-time;
- daily;
- weekdays;
- weekly;
- interval.

For interval reminders the UI exposes:

- recurrence every;
- recurrence unit.

For cancelled reminders the UI exposes:

- a restore action for the creator;
- restore-through-edit flow with a new future date/time;
- historical cancelled entries preserved under the same reminder card.

## 9. Care UX

Current care experience has three layers:

1. template browser
2. custom care composer
3. care history

Important behaviors:

- templates are the primary send path;
- category selection narrows the catalog;
- custom care is available but secondary;
- history is collapsible and refreshable;
- inbound sent items can expose quick replies.

## 10. Duplicate-submit protection

The current frontend already protects major actions from double clicks using a shared busy-wrapper pattern.

This is used for:

- invite creation and cancellation;
- accept/reject invite;
- unpair;
- reminder create/update/restore/cancel;
- care send and reply;
- profile save;
- refresh actions.

This is important because the backend also now contains duplicate-submit reuse logic for reminders and custom care. The UI and backend protections work together.

## 11. Error handling

Current UI behavior:

- bootstrap auth failure becomes a fatal screen state;
- ordinary request failures surface through inline error handling;
- `window.onerror` and `unhandledrejection` are surfaced to the UI;
- the UI can recover through manual or automatic refresh.

What matters:

- do not hide auth/session failures;
- do not silently ignore request errors;
- do not leave buttons active during in-flight mutations.

## 12. Mini App flow rules

The frontend must preserve these product rules:

- all pair/reminder/care state comes from backend responses;
- invite preview is informational until the backend confirms the valid lifecycle;
- reminders are always shown as pair-scoped;
- care history is always shown as pair-scoped;
- the active pair is the gate to all reminder/care workspaces.

## 13. Visual and copy guidance

The current design intent is:

- calm;
- compact on mobile;
- resilient to narrow Telegram mobile viewports;
- non-technical;
- Telegram-compatible;
- high-contrast enough for light and dark themes.

Avoid turning the Mini App into:

- a generic admin dashboard;
- a verbose technical panel;
- a multi-column desktop-first layout.

Copy should stay product-facing and avoid internal state terminology like:

- `rule_id`
- `dispatch`
- `occurrence`
- `processing`

## 14. Current frontend risks

- frontend is still a single-file monolith;
- DOM wiring is manual and can regress easily during refactor;
- there are no typed frontend models;
- bootstrap correctness depends on preserving the auth response contract.

## 15. Safe continuation path

If the frontend is refactored, keep the sequence:

1. extract API client helpers;
2. extract pure render/state helpers;
3. extract reminder and care modules;
4. only then consider framework migration.

Do not combine:

- API contract changes;
- UX rewrites;
- frontend module extraction

in one release unless there is end-to-end Telegram Mini App verification.

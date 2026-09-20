# FixFlow Frontend

## Scope

The Task 9 UI is a Vite/React/TypeScript application using Ant Design as its
only component framework. Routes are `/login`, `/resident`, and `/operator`.
It is explicitly labelled **Development Demonstration Mode** because language
and embedding providers are deterministic offline demonstrations.

The resident page supports login/logout, authorised property selection, a new
or continued Agent conversation, live SSE status, the three typed interrupt
forms, and refreshed ticket/appointment summaries. Rescheduling and requests
for human handling use the Agent path. When a completed visit is awaiting
confirmation, the resident can confirm that the issue is fixed and close the
ticket. The UI does not ask the resident to record each unsuccessful repair
attempt; while the worker remains on site, they continue directly.

The operator page provides a dedicated pre-ticket “待人工处理” queue, ticket
status/category/severity filters, business details, ticket and appointment
histories, and the latest Worker Event. It also records the three material visit
events: work started, repair completed pending resident confirmation, and visit
failed with a reason requiring follow-up or escalation. Travel-status clicks are
not required to begin an on-site repair. Queue items are ordered by priority,
filterable by lifecycle status, searchable, and use the existing optimistic
`OPEN`/`CLAIMED`/`RESOLVED`/`DISMISSED` transitions. Version conflicts refresh
the authoritative queue instead of overwriting another operator. Details are
presented as business summary, handling basis, and a collapsed technical-audit
layer. A known
Agent thread can be inspected by the Operator Thread API only when its Agent
State ticket link is revalidated against the database resident/property ticket
snapshot. The UI never guesses a relation or fabricates policy, workflow, or
ticket data. For an authorized, ticket-linked thread the workbench shows a paged,
source-filterable persistent
execution timeline. It labels business histories and Agent Trace as separate
views and renders a safe summary rather than raw JSON.

## Security boundary

Only the token and current `thread_id` are retained in `sessionStorage`; the
token is never put in a URL or log. On refresh `/auth/me` rebuilds the trusted
user/role, invalid tokens are removed, and Thread State is fetched again.
Passwords, complete Agent State, and policy evidence are not stored. SSE uses
authenticated `fetch` with a Bearer header and an `AbortController`, not native
`EventSource` or a query token. Reconnect performs another Thread State
reconciliation. The frontend never submits `actor_id` or `actor_type`, and route guards
are presentation only—the API enforces permission. React default escaping is
used; no `dangerouslySetInnerHTML` rendering exists.

## Conversation delivery

The resident page is a fixed-height application shell. Its recent-conversation
rail and message region scroll independently, while the composer remains
anchored at the bottom of the chat region. The rail shows at most the five most
recently active conversations. “全部会话” opens a searchable drawer with
active/archived filters and archive/restore controls. Every active conversation
uses the single action label “归档会话”. In the archived view, conversations with
an in-progress ticket can only be restored; eligible conversations can also be
permanently removed after a destructive confirmation. Permanent removal cannot
be restored, but it never cancels or deletes Checkpoint, Trace, Replay, ticket,
appointment, or audit facts.

Resident-visible workflow, ticket, severity, and missing-field labels are
Chinese business language. Internal enum names, worker UUIDs, SSE terminology,
and booking implementation flags are not rendered. In-progress calls show the
current authoritative stage (understanding, policy check, ticket creation,
slot lookup, result reconciliation, or human handoff). A failed request keeps
its HTTP result boundary visible and offers retry or property-staff assistance;
SSE remains a notification channel rather than the source of truth.

For a scheduled or in-progress visit, the resident ticket card exposes
“确认已修好”. The resident uses it only after confirming the result with the
worker on site; property staff do not need to submit a separate completion
step first. “申请改期” starts a reschedule request without inventing an
availability window, then asks the resident to provide the desired date and
time range.

The public SSE terminal events are `message.completed`, `message.failed`, and
`message.escalated`. HTTP and the refreshed Thread State remain authoritative;
the browser de-duplicates events by Run/event identity and falls back to state
refresh after a malformed or disconnected stream.

## Local commands

Use Node.js 20 or newer:

```bash
powershell -ExecutionPolicy Bypass -File scripts/operations/frontend.ps1 dev
powershell -ExecutionPolicy Bypass -File scripts/operations/frontend.ps1 lint
powershell -ExecutionPolicy Bypass -File scripts/operations/frontend.ps1 typecheck
powershell -ExecutionPolicy Bypass -File scripts/operations/frontend.ps1 test
powershell -ExecutionPolicy Bypass -File scripts/operations/frontend.ps1 build
```

Run these commands from the repository root. The wrapper selects Node 20 or
newer and invokes the checked-in frontend tools directly, avoiding the outdated
system `npm` runtime on this workstation.

The default API is `http://127.0.0.1:8000`; set `VITE_API_BASE_URL` only for a
different local endpoint. Vite defaults to `http://127.0.0.1:5173`.

Vitest and React Testing Library cover login, safe errors, resident messages,
every interrupt control, ticket summary, role routing, and operator listing. A
heavy browser E2E framework is intentionally not introduced in Task 9.

Resident, operator, and login routes are loaded independently with
`React.lazy`, avoiding one synchronous page entry. Ant Design remains the only
UI framework; no complex bundler plugin is introduced. API, MCP, and frontend
still start as documented separate processes, so one-command Compose delivery
remains partial.

Task 11 adds resident reconciliation states and the operator “失败对账” panel.
Pending/processing disables mutation-producing controls and asks the resident not
to resubmit; manual review remains blocked for property staff. A confirmed
not-committed result returns control to the original Resume path and a committed
result is rendered only after the API refreshes PostgreSQL snapshots. Operators
can filter, page, inspect a sanitized detail drawer, refresh, and request a
PENDING-only read recheck. No UI can mark a result or replay a mutation.

Resident `REQUEST_HUMAN` only displays that property staff must handle the
request; it never claims that a ticket was escalated. The Operator ticket drawer
owns the formal escalation control. An HTTP 202 disables that control and shows
the Case status. `RESOLVED_COMMITTED` refreshes the ticket,
`RESOLVED_NOT_COMMITTED` re-enables a same-payload retry with the original API
idempotency key, and `MANUAL_REVIEW` remains blocked without a force button.

## Recovery Console

Task 12 embeds an Operator-only Recovery Console beneath an authorized thread
review. It lists original Runs and replayability, displays Bundle schema and
checksum metadata, launches the sole safe action “验证确定性重放”, and renders
PASSED, DIVERGED, INCOMPLETE, UNSUPPORTED_SCHEMA, or FAILED_SAFE with bounded
mismatch summaries and a deterministic recommendation.

Original Replay evidence and current PostgreSQL business state are separate
cards. The browser never receives raw tape JSON, complete Agent State,
Checkpoint data, idempotency material, or Provider/MCP payloads. There is no
button to apply Replay, resend a mutation, restore a database, or modify a
Checkpoint. Resident pages do not expose Replay internals.

# FixFlow API

## Runtime boundary

Task 13 does not change public request or response contracts. The server may
select GLM-5.1 for structured interpretation at startup, but clients cannot
override provider, model, base URL, key, timeout, retry policy, or prompt
version. Final HTTP state remains authoritative even if an interpretation call
fails and SSE remains only a delivery aid.

Task 9 exposes the existing deterministic system through FastAPI. The supported
call path is:

```text
Frontend -> FastAPI -> authenticated caller context
         -> AgentOrchestrator / Application query service
         -> MCP / Application service -> PostgreSQL
```

Routers do not query ORM models, invoke MCP tool names, create business
idempotency keys, or implement state transitions. JWT identity establishes the
caller but never replaces the database property-authorisation check.

## Authentication

`POST /api/v1/auth/login` accepts `username` and `password`; `GET
/api/v1/auth/me` returns the current safe user view. Passwords are verified with
Argon2 and only hashes are stored. Failed logins deliberately do not disclose
whether a username exists.

Access tokens use `FIXFLOW_JWT_ALGORITHM=HS256`, a secret from
`FIXFLOW_JWT_SECRET`, and `FIXFLOW_JWT_ACCESS_TOKEN_MINUTES`. Startup rejects a
missing, empty, placeholder, or shorter-than-32-byte secret and never generates
a replacement at runtime. Required claims are `sub`,
`actor_type`, `user_id`, `iat`, `exp`, and `token_id`. `property_id` is not a
claim. Tokens with a missing claim, bad signature, expired time, or unapproved
algorithm are rejected.
Every authenticated request reloads the account; disabling a user invalidates
an otherwise unexpired token. `FIXFLOW_CORS_ORIGINS` is an explicit development
allow-list. Wildcard origins are rejected and CORS is not authentication.

## Routes

| Method | Route | Role | Purpose |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/login` | public | issue an access token |
| GET | `/api/v1/auth/me` | authenticated | safe caller profile |
| GET | `/api/v1/resident/properties` | resident | authorised properties |
| GET | `/api/v1/resident/tickets` | resident | paged own tickets |
| GET | `/api/v1/resident/tickets/{ticket_id}` | resident | own ticket detail |
| POST | `/api/v1/agent/threads` | resident | create a verified thread |
| POST | `/api/v1/agent/threads/{thread_id}/messages` | resident | continue a thread |
| POST | `/api/v1/agent/threads/{thread_id}/resume` | resident | strict typed resume |
| GET | `/api/v1/agent/threads?archive_status=active|archived|all` | resident | recent/all thread registry |
| POST | `/api/v1/agent/threads/{thread_id}/archive` | resident | recoverable soft archive |
| POST | `/api/v1/agent/threads/{thread_id}/restore` | resident | restore archived thread |
| GET | `/api/v1/agent/threads/{thread_id}` | resident | sanitised state view |
| GET | `/api/v1/agent/threads/{thread_id}/events` | resident | authenticated SSE |
| GET | `/api/v1/operator/tickets` | operator | filtered paged work list |
| GET | `/api/v1/operator/tickets/{ticket_id}` | operator | histories and latest event |
| GET | `/api/v1/operator/threads/{thread_id}` | operator | known thread review |
| GET | `/api/v1/operator/threads/{thread_id}/runs` | operator | paged sanitized runs |
| GET | `/api/v1/operator/runs/{run_id}` | operator | one authorized run |
| GET | `/api/v1/operator/runs/{run_id}/events` | operator | paged/filterable trace events |
| POST | `/api/v1/operator/tickets/{ticket_id}/escalate` | operator | formal escalation service |
| GET | `/api/v1/operator/human-review-cases` | operator | pre-ticket review queue |
| POST | `/api/v1/operator/human-review-cases/{case_id}/transition` | operator | typed optimistic transition |
| GET | `/api/v1/operator/human-review-cases/{case_id}/events` | operator | immutable case history |

The resume variants are `PROVIDE_INFORMATION`, `SELECT_DUPLICATE_TICKET`, and
`SELECT_APPOINTMENT_SLOT`. They form a discriminated Pydantic union; arbitrary
dictionaries and identity overrides are rejected. Thread responses expose only
safe workflow/run status, assistant text, typed interrupt, refreshed business
summaries, structured issue view, and policy status. Checkpoint blobs, raw MCP
responses, hashes, SQL, and stacks are never returned.

The four mutation routes (thread creation, message, Resume, and operator
escalation) require `Idempotency-Key`. The Task 9 API keeps a bounded,
single-process replay record keyed by caller, route scope, and key. An identical
payload replays the same HTTP result; a different payload returns `409
IDEMPOTENCY_CONFLICT`. This API retry boundary is separate from durable
Application/Tool idempotency. API replay records do not survive process restart.
When persistent Trace is available, a replay or same-key conflict may emit a
runless sanitized API audit event with a new correlation ID, the original Run
ID where known, and only a SHA-256 key fingerprint. It never starts another
Graph Run and never persists the raw `Idempotency-Key`.

Operator thread review is a dedicated read-only projection. An active Operator
may inspect only a thread whose `active_ticket_id` resolves to a real ticket
matching the thread resident and property. Ordinary Resident `get_state`, raw
checkpoints, conversation messages, pending-operation hashes, and internal
policy score/text structures are unavailable. Pre-ticket failures are exposed
only through the sanitized human-review queue; they are not made visible by
inventing a ticket or bypassing thread ownership.

## SSE

The browser uses `fetch()` with `Authorization: Bearer ...` and reads its
`ReadableStream`; it does not use native `EventSource` or a query token. `GET
/api/v1/agent/threads/{thread_id}/events` authenticates and rechecks
thread/property ownership before registering a subscriber. Events contain
`event_id`, `run_id`, `sequence`, `event_type`, `thread_id`, `trace_id`,
`timestamp`, and safe `data`. Types are `run_started`, `assistant_delta`,
`workflow_updated`, `message.completed`, `message.failed`,
`message.escalated`, and `heartbeat`.

Task 9 uses a per-thread bounded, live in-memory event bus. It does not cache
without subscribers or promise restart replay. A run publishes `run_started`,
zero or more workflow/delta events, then exactly one public `message.*`
terminal. On overflow stale non-terminal events
are evicted first, the latest workflow update replaces an older one, and
terminal events take priority. `assistant_delta` is a
demonstration chunk, not provider token streaming. SSE is not a business fact
source: every mutation response already contains the final result. On disconnect
or reconnect the browser calls Thread State to reconcile. `Last-Event-ID` does
not replay history, and process restart loses SSE events. Persistent Trace is
separately queryable and does not change these SSE delivery semantics.

## Persistent execution review

Task 10 assigns a server-generated `run_id` to each thread creation, message,
Resume, and formal operator action. Trace start is committed before Graph
execution. The operator run/event routes reuse the existing ticket-linked
thread review boundary; arbitrary identifiers do not grant access. Responses
contain only the closed, sanitized Trace projection and never expose checkpoint
state, conversations, prompts, raw tool/provider responses, SQL, or credentials.
Source filtering and bounded `limit`/`offset` pagination are supported.

## Errors

Errors use `code`, `message`, `field_errors`, `trace_id`, and `retryable`.
Stable mappings cover invalid/expired tokens, permissions, thread identity,
property context, validation, version/time conflicts, stale or mismatched
resumes, unavailable services, and safe internal errors. SQL, constraint names,
password hashes, and exception stacks are never client-visible.

On Windows, start the API with `uv run python -m app.api.run` after setting
`PYTHONPATH=backend`. The entry point applies the Selector event-loop policy
required by the official asynchronous PostgreSQL checkpointer before Uvicorn
creates its loop. Calling Uvicorn directly is not the supported Windows path.

## UNKNOWN_COMMIT responses and operator review

Resident thread creation, message, and Resume return HTTP 202 with
`error_code=RECONCILIATION_PENDING`, `run_status=FAILED_SAFE`, the frozen stage,
and a minimal reconciliation projection when mutation delivery is uncertain.
Repeating the same API `Idempotency-Key` returns the first response and does not
create another Run, operation, or Case.

Operators use `GET /api/v1/operator/reconciliation/cases`, its detail route, and
`POST .../{case_id}/recheck`. The reviewing account must be an active Operator.
Resident-action cases retain their frozen resident/property authority evidence;
Operator escalation cases retain the original Operator identity and the formal
ticket/property link. Responses omit raw keys,
payloads, Checkpoint state, SQL, and exceptions. Recheck is PENDING-only and
never writes a resolution or resends the mutation.

`POST /api/v1/operator/tickets/{ticket_id}/escalate` is the sole escalation
mutation entry and requires an active Operator JWT. Resident `REQUEST_HUMAN`
does not call it. Unknown delivery returns HTTP 202 with a minimal Case. While
the Case is unresolved, committed, or manual, same-key API replay returns the
cached 202 and never starts another Action Run. A formal same-payload, same-key
retry is released only after `RESOLVED_NOT_COMMITTED`; committed and inconsistent
outcomes are never automatically resent.

## Operator deterministic Replay

Task 12 adds an Operator-only, read-only Recovery Console API:

```text
GET  /api/v1/operator/threads/{thread_id}/replay-runs
GET  /api/v1/operator/runs/{run_id}/replay
GET  /api/v1/operator/replay-bundles/{bundle_id}
POST /api/v1/operator/runs/{run_id}/replay/verify
GET  /api/v1/operator/replay-executions/{execution_id}
```

Authorization reuses the ticket-linked Operator Trace boundary. Identifiers
alone are not capabilities. Operator-action runs additionally prove their
typed target-ticket link through the Application query service. Residents
receive 403 and unrelated IDs return 404/403.

Verify requires `Idempotency-Key`; identical Operator/key/request replays the
same diagnostic Execution and conflicting reuse returns 409. Responses contain
only schema/revision/integrity metadata, closed statuses, safe mismatch
summaries, and a read-only recommendation—never raw tape, conversations,
Checkpoint data, prompts, credentials, SQL, or exception stacks.

# MCP contracts

## Service and trust boundary

`property-operations-mcp` is an independent process using the official Python
MCP SDK and Streamable HTTP at `/mcp`. It is not mounted in the FastAPI process.

```text
MCP Tool -> MCPApplicationAdapter -> Application Service / Query Service
         -> Repository Port / Unit of Work -> PostgreSQL
```

Tools validate Pydantic input, call the adapter, and return typed output. They do
not import ORM models or Repository implementations and do not own authorization,
transactions, idempotency, state transitions, or optimistic locking. The server
currently trusts that its upstream caller supplies an authenticated actor
identity; the Application layer still checks that identity against database-backed
business authorization. JWT authentication is deferred to Stage B.

Start the process locally with:

```powershell
uv run python -m mcp_server
```

`MCP_HOST`, `MCP_PORT`, and `DATABASE_URL` are environment settings. Defaults are
host `127.0.0.1`, port `8765`, and path `/mcp`. Startup logs include the service
name, transport, host, and port, but never the database URL.

## Implemented tool set

| Tool | Typed request | Typed data result |
| --- | --- | --- |
| `get_resident_property` | actor metadata, `resident_id`, `property_id` | authorized property identity and address fields |
| `find_open_repair_tickets` | actor metadata, resident/property/category, `normalized_issue_location` | exact structured non-terminal candidates |
| `create_repair_ticket` | mutation metadata, resident/property/category, issue fields, severity | created ticket reference/version/status |
| `get_ticket_snapshot` | actor metadata, `ticket_id` | current ticket, active appointment, and latest worker event read model |
| `list_available_slots` | actor metadata, property/category, aware search window, duration, limit | deterministic candidate slots and ranking evidence |
| `book_appointment` | mutation metadata, ticket/worker/time interval, ticket version | created appointment and resulting ticket state |
| `reschedule_appointment` | mutation metadata, ticket/appointment/worker/time interval, both versions | replacement appointment and resulting versions |
| `escalate_to_operator` | mutation metadata, ticket version, typed reason, explanation/evidence | updated ticket reference/version/status |

The first release does not expose `record_worker_event`,
`record_resident_acceptance`, `cancel_appointment`, `cancel_ticket`, or
`resolve_escalation`. These remain later incremental tools; generic status setters
are forbidden.

## Request contracts

All requests forbid undeclared top-level fields. Read requests require:

```text
actor_type: ActorType
actor_id: UUID
trace_id: UUID
```

Mutations additionally require a non-empty, length-limited:

```text
idempotency_key: string
```

Mutations of existing aggregates also require positive versions. Booking carries
the ticket `expected_version`; rescheduling carries both `expected_version` and
`expected_appointment_version`. Creating a ticket has no existing aggregate and
therefore does not invent `expected_version=0`. Timestamps are timezone-aware and
interval ends must be after starts. Enum and UUID fields are validated by
Pydantic rather than accepted as free JSON.

## Response envelope

Every tool returns:

```text
contract_version: "1.0"
result_code: ResultCode
message: string
data: tool-specific schema | null
error: {code, message, field_errors, retryable} | null
trace_id: UUID
```

Implemented result codes are `FOUND`, `CREATED`, `UPDATED`, `ALREADY_EXISTS`,
`NOT_FOUND`, `VERSION_CONFLICT`, `VALIDATION_ERROR`, `PERMISSION_DENIED`,
`TIME_CONFLICT`, `OPERATION_IN_PROGRESS`, `IDEMPOTENCY_CONFLICT`,
`UNKNOWN_COMMIT`, `SERVICE_UNAVAILABLE`, and `INTERNAL_ERROR`. The latter two
provide stable, sanitized messages; SQL, constraint names, stack traces, and
database connection details are never protocol output.

The adapter centrally maps stable Application error codes. Important mappings
include:

| Application code | MCP result | Retry meaning |
| --- | --- | --- |
| missing property/ticket/appointment | `NOT_FOUND` | do not blind retry |
| authorization failure | `PERMISSION_DENIED` | no business write occurred |
| `version_conflict` | `VERSION_CONFLICT` | reread before deciding |
| `active_appointment_exists` | `ALREADY_EXISTS` | reread ticket snapshot |
| `appointment_time_conflict` | `TIME_CONFLICT` | select another candidate |
| `worker_not_eligible` | `VALIDATION_ERROR` | correct worker or interval |
| `idempotency_payload_conflict` | `IDEMPOTENCY_CONFLICT` | use original payload or a new key |
| `idempotency_request_in_progress` | `OPERATION_IN_PROGRESS` | retry/reconcile later |
| exact duplicate rejected | `ALREADY_EXISTS` | inspect returned workflow state |

`UNKNOWN_COMMIT` recovery and infrastructure availability classification are
reserved contract semantics for the Stage-C reliability implementation; Task 5
does not claim that recovery path is complete.

## Exact open-ticket candidates

`find_open_repair_tickets` filters only the same resident, property, frozen issue
category, normalized exact location, and non-terminal status. Its response labels
the match as `EXACT_STRUCTURED_CANDIDATE`. It is not semantic duplicate detection:
there is no LLM, embedding, fuzzy address logic, or similarity threshold.

## Deterministic candidate slots

`SLOT_GRANULARITY_MINUTES = 30` is centralized in the Application query module.
Starts align to natural-clock boundaries (`09:00`, `09:30`, and so on); a search
starting at `09:10` first considers `09:30`. The caller must explicitly supply
`requested_duration_minutes`; no default repair duration is inferred. The full
candidate must fit the search window and one availability window and must not
overlap a current `BOOKED` appointment.

A worker is eligible only when active, carrying the frozen category-to-skill
mapping, and in the exact normalized service area. Release 1 compares
`property.community_name` with `worker.service_area`; normalization only trims,
case-folds, and collapses consecutive whitespace. It does not use fuzzy matching,
LLMs, embeddings, or an area map.

Ordering is strictly:

```text
1. candidate_start ascending
2. open_ticket_count ascending
3. worker_id ascending
```

The response includes `service_area_matched=true`, the service area, workload,
rank, and `slot_granularity_minutes=30` as explanations. Area is a hard filter and
does not participate in ranking. Identical input and database state produce the
same order. A candidate is not a booking guarantee: `book_appointment` still uses
optimistic locking, the one-BOOKED-per-ticket unique index, and the GiST worker
overlap constraint, so a later concurrent claim can return `TIME_CONFLICT`.

`open_ticket_count` is the number of distinct, non-terminal tickets currently
associated with the worker through a `BOOKED` appointment. The repository uses
one grouped `COUNT(DISTINCT ticket_id)` query for all candidate workers and
excludes tickets in `CANCELLED` or `CLOSED`. Historical `SUPERSEDED`, `CANCELLED`,
`NO_SHOW`, or other non-`BOOKED` appointments therefore neither create workload
nor duplicate a ticket count. This is a stable snapshot calculation and does not
issue one workload query per worker.

## Composition root and lifecycle

The server constructs one async engine, session factory, Unit-of-Work factory,
Application facade, MCP adapter, and FastMCP server. No database connection is
opened during module import, tools do not build object graphs, each Application
call creates an independent Unit of Work, and shutdown disposes the engine. No DI
container or global mutable session is used.

## Contract evidence and deferred work

Tests cover generated schemas for all eight tools, adapter forwarding and safe
error mapping, all eight tools through the production Application layer and real
PostgreSQL, deterministic slot eligibility/order, authorization and no-partial-
write failures, idempotency/version/time conflicts, and a real MCP SDK client over
a random-port Streamable HTTP server. The transport test initializes, lists tools,
reads schemas, invokes read and mutation tools, receives structured results, and
shuts down the server.

LangGraph, LLMs, policy RAG, JWT, FastAPI business routes, Agent-runtime MCP
client integration, Outbox, Trace runtime, recovery, frontend, Harness, and
evaluation remain deferred and mandatory roadmap work.

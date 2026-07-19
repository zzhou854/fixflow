# MCP contracts

## Service boundary

The independent service is named `property-operations-mcp`. It uses the official
Python MCP SDK and Streamable HTTP. It does not run in the FastAPI process.

MCP handlers validate Pydantic request schemas and call application services.
They do not contain authorization rules, domain transitions, idempotency logic,
or ORM transaction management. Fake MCP in the Engineering Harness must reuse
the same request/result models.

## Initial tool set

Week 1 should implement only tools justified by an application service:

1. `get_resident_property`
2. `find_open_repair_tickets`
3. `create_repair_ticket`
4. `get_ticket_snapshot`
5. `list_available_slots`
6. `book_appointment`
7. `reschedule_appointment`
8. `escalate_to_operator`

Possible later additions require a demonstrated workflow need:
`cancel_appointment`, `record_resident_acceptance`,
`resolve_ticket_escalation`, and `record_worker_event`. A generic
`update_ticket_status` tool is excluded because it would bypass domain commands
and invariants.

## Shared mutation metadata

Every mutation of an existing aggregate includes:

```text
actor_id: UUID
trace_id: UUID
idempotency_key: string
expected_version: integer
```

Create operations have no aggregate version yet, so they require the first three
fields plus typed preconditions defined by that command. A command that changes a
ticket and appointment atomically carries both expected versions.

Requests also contain typed domain identifiers and parameters. Free-text
business arguments are not accepted. Status strings are never generic setter
arguments; command-specific tools express booking, rescheduling, cancellation,
acceptance, escalation/resolution, and worker-event recording.

## Result envelope

Tools return a typed business result, not a Boolean success flag.

Candidate result codes:

```text
CREATED
UPDATED
ALREADY_EXISTS
VERSION_CONFLICT
VALIDATION_ERROR
PERMISSION_DENIED
TIME_CONFLICT
UNKNOWN_COMMIT
SERVICE_UNAVAILABLE
```

The envelope should contain:

```text
code
resource_type
resource_id
resource_version
message
retryable
current_snapshot (only when safe and necessary)
```

`message` is explanatory data and never replaces `code`. Sensitive or
unauthorized snapshots must not be returned.

## Contract sketches

The following are documentation-level candidates, not implemented schemas.

### `create_repair_ticket`

Request domain fields: `resident_id`, `property_id`, `issue_category`,
`issue_location`, `issue_description`, `severity`, plus mutation metadata.

The service rechecks the resident-property relation, required fields, duplicate
open tickets, and idempotency/duplicate preconditions before committing.

### `book_appointment`

Request domain fields: `ticket_id`, `worker_id`, `starts_at`, `ends_at`, plus
mutation metadata. The service validates ticket state, worker skill/area,
availability, and the PostgreSQL overlap constraint.

### `reschedule_appointment`

Request domain fields: `ticket_id`, `appointment_id`, new interval/worker, plus
mutation metadata. It marks the prior appointment `SUPERSEDED` and creates a new
row in one transaction; it never overwrites the old interval.

The request carries current ticket and appointment versions. Candidate slots do
not create rows; only a resident-confirmed replacement is committed.

### `escalate_to_operator`

Request domain fields: `ticket_id`, typed `reason_code`, safe evidence IDs, plus
mutation metadata. It must not expose private policy or conversation data beyond
the operator's authorization.

### `record_worker_event` (when introduced)

Request fields include `ticket_id`, `appointment_id`, `subject_worker_id`, typed
event/reason/evidence, and the real recording actor plus mutation metadata.
Operator simulation records the operator as actor and never impersonates the
worker. The application service validates canonical order and performs any
ticket/appointment transitions atomically.

`ACCEPTED` and `REJECTED` require a current `BOOKED` appointment; release 1 has no
separate worker-assignment entity. Acceptance changes neither aggregate.
Rejection atomically cancels the appointment with worker actor metadata and
`WORKER_REJECTED`. An `INITIAL_REPAIR` appointment returns the ticket to `OPEN`;
a `REWORK` appointment returns it to `REWORK_REQUIRED` without changing the
existing rework count. Purpose is typed input and must match the current ticket
snapshot.
`COMPLETED` fulfills the appointment and moves the ticket to acceptance, never
directly to closure. `FAILED_TO_COMPLETE` fulfills the appointment and uses its
typed cause to select same-ticket rework or escalation.

Same source key and request hash returns the existing typed result; the same key
with a changed payload is an idempotency conflict. Invalid order creates no
canonical worker event or domain mutation and is retained in Trace/audit telemetry.

## Transaction and retry semantics

- The application service owns the database transaction.
- A unique idempotency record protects each mutating operation.
- `VERSION_CONFLICT` is not blindly retryable; the caller must reread state.
- `TIME_CONFLICT` requires new slot selection.
- `UNKNOWN_COMMIT` requires state reconciliation by idempotency key or snapshot
  lookup before any retry.
- Malformed MCP responses fail schema validation and do not advance workflow state.
- Phase-3 Outbox records are written with the corresponding business mutation.

## Authorization

Authorization is checked before calling a mutating MCP tool and again inside the
application service. A resident can act only on related properties/tickets. An
operator can perform workbench operations. All mutations record `actor_id` and
`trace_id`; denied attempts are later recorded in business Trace.

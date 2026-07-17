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
`cancel_appointment`, `update_ticket_status`,
`record_resident_acceptance`, and `record_worker_event`.

## Shared mutation metadata

Every mutating tool request includes:

```text
actor_id: UUID
trace_id: UUID
idempotency_key: string
expected_version: integer
```

It also contains typed domain identifiers and parameters. Free-text business
arguments are not accepted by mutation tools.

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
open tickets, and current intent/version preconditions before committing.

### `book_appointment`

Request domain fields: `ticket_id`, `worker_id`, `starts_at`, `ends_at`, plus
mutation metadata. The service validates ticket state, worker skill/area,
availability, and the PostgreSQL overlap constraint.

### `reschedule_appointment`

Request domain fields: `ticket_id`, `appointment_id`, new interval/worker, plus
mutation metadata. It marks the prior appointment `SUPERSEDED` and creates a new
row in one transaction; it never overwrites the old interval.

### `escalate_to_operator`

Request domain fields: `ticket_id`, typed `reason_code`, safe evidence IDs, plus
mutation metadata. It must not expose private policy or conversation data beyond
the operator's authorization.

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


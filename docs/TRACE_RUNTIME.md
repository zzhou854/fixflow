# Persistent Trace Runtime

Trace is FixFlow's sanitized execution-control and audit plane. It explains an
Agent/API/MCP run; it is not the source of ticket or appointment truth and it
does not replace the LangGraph Checkpoint database.

## Identity and lifecycle

`thread_id` identifies a conversation, `run_id` one message/resume/operator
action, `trace_id` request correlation, and `operation_id` one business
mutation. Only the server creates these identities. A run is started and its
first `run_started` event is committed before the Graph is invoked. If this
write fails, business execution does not start.

Run status is one of `RUNNING`, `INTERRUPTED`, `COMPLETED`, `FAILED_SAFE`, or
`FAILED`; trigger is `THREAD_CREATED`, `MESSAGE`, `RESUME`, or
`OPERATOR_ACTION`. Per-run sequence numbers are allocated while locking the run
row, so concurrent writers remain unique and gap-free. A lifecycle terminal is
exactly one of `run_interrupted`, `run_completed`, `run_failed_safe`, or
`run_failed`. It is inserted with the final status, terminal marker, finish
time, and next sequence in one transaction; a duplicate same result is
idempotent and a conflicting result is rejected.

Sources are `API`, `AGENT`, `MCP`, `DOMAIN`, and `OUTBOX`. Graph nodes emit
started/completed/failed events, MCP calls emit prepared/completed/failed
events, and dispatched domain events appear as DOMAIN events. An externally
originated domain event may have no `run_id`; the projector never invents one.

Terminal does not mean that the timeline has no future evidence. After a Run
has ended, only asynchronous `DOMAIN` or `OUTBOX` audit events may be appended;
they lock the same Run and receive a later sequence without changing the final
status or finish time. Control-plane Agent/MCP/API events are rejected after
terminal. Runless events have `run_id = sequence_number = NULL`, use their
unique event key for idempotency, and never alter a Run.

HTTP idempotency replay never starts another Run or re-invokes the Graph. It
returns the original response and may write a runless `api_request_replayed`
event containing only the original run ID and a SHA-256 key fingerprint. A
same-key/different-payload rejection may similarly write runless
`api_request_conflict`; raw `Idempotency-Key` values are never persisted.
Failure to atomically start the initial Run/event removes the transient API
reservation, so the same key can safely retry.

## Sanitization

Persistence accepts a closed Pydantic `TracePayload`, not an arbitrary
dictionary. A recursive guard rejects credential-like keys including
authorization, tokens, JWT, passwords, secrets, database URLs, API keys and
cookies. Strings and total payload bytes are bounded by
`FIXFLOW_TRACE_MAX_STRING_LENGTH` and `FIXFLOW_TRACE_MAX_PAYLOAD_BYTES`.
Trace never stores full conversation messages, checkpoint blobs, chain of
thought, ORM representations, raw provider/MCP responses, SQL, or exception
stacks.

## Operator review

The operator APIs list runs for a thread and sanitized events for a run. They
first reuse the ticket-linked Task 9.1 review boundary: the checkpoint must
contain an active ticket and the current PostgreSQL snapshot must match the
resident and property. Knowing a thread/run UUID is insufficient. Residents
cannot access these routes. Responses expose only the safe projection.

The workbench shows Agent Trace separately from ticket and appointment status
history. This is an execution timeline, not a fabricated business timeline.

SSE remains a bounded, nonpersistent presentation channel. HTTP responses and
the thread-state endpoint remain the formal result, and reconnects still
reconcile through thread state. Persistent Trace does not turn SSE into a
business fact source.

Task 10 enables audit visibility and durable domain-event evidence. General
UNKNOWN_COMMIT reconciliation, the fault harness, and deterministic Replay are
explicitly deferred.

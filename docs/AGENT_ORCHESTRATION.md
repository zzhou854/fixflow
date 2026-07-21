# Agent orchestration

## Scope and truth boundary

Task 8 adds one asynchronous LangGraph `StateGraph`. It coordinates the typed
Agent core, policy retrieval, and the independent property-operations MCP
server. PostgreSQL business tables remain the source of ticket and appointment
truth. A checkpoint contains bounded conversation work state only and is never
written back over a fresher business snapshot.

The current runtime uses a Scripted LLM and deterministic test embedding in its
tests. Online providers, FastAPI/JWT, SSE, frontend, Trace, Outbox, Harness, and
Stage C recovery remain deferred.

## Nodes and deterministic routing

```mermaid
flowchart TD
    Start --> Property[resolve_property]
    Property --> Interpret[interpret]
    Interpret -->|safety| Emergency[EMERGENCY_REVIEW]
    Interpret -->|missing fields| Info[Need Information interrupt]
    Info --> Interpret
    Interpret -->|new repair| Policy[retrieve_policy]
    Policy -->|conflict or insufficient| Human[HUMAN_REVIEW]
    Policy --> Duplicate[find_duplicates]
    Duplicate -->|candidate| PickTicket[Duplicate Selection interrupt]
    Duplicate -->|none| PrepareCreate[prepare create]
    PrepareCreate --> Create[create ticket]
    Create --> Snapshot[refresh snapshot]
    PickTicket --> Snapshot
    Snapshot --> Slots[list slots]
    Slots --> PickSlot[Slot Selection interrupt]
    PickSlot --> Snapshot
    Snapshot --> PrepareBook[prepare book or reschedule]
    PrepareBook --> Mutate[book or reschedule]
    Mutate --> Snapshot
    Snapshot --> Compose[compose verified response]
```

Routers inspect typed intent, risk, policy sufficiency, current candidates, and
fresh snapshot facts. LLM output cannot name a node or tool. Policy text cannot
select a tool. Only approved mutation nodes call mutation methods.

Automated paths are `NEW_REPAIR`, `QUERY_TICKET_STATUS`,
`RESCHEDULE_APPOINTMENT`, and `REQUEST_HUMAN`. Cancellation and resident
acceptance/rejection intents are recognized but routed to `HUMAN_REVIEW`
because the eight-tool MCP contract does not yet expose those operations.

## Thread owner identity, property authorisation, and duration

A new thread requires trusted `actor_type`, `actor_id`, `user_id`, and
`property_id`. Only after `get_resident_property` succeeds are those values
frozen as one `ThreadOwnerIdentity`. A supplied property ID alone is never an
authorisation decision. Missing property context returns
`PROPERTY_CONTEXT_REQUIRED`; the chat is not asked to supply a UUID.

Every ordinary continuation compares its trusted actor/user tuple (and a
supplied property, if any) with that immutable owner identity, then calls
`get_resident_property` again **before** interpretation, policy retrieval, or a
business tool. `resume()` and `get_state()` require `AgentCallerContext` and
perform the same owner comparison; resume also reauthorises the property before
validating its Interrupt kind and fingerprint. A mismatch returns a sanitized
`THREAD_IDENTITY_CONFLICT` with no checkpoint state, interrupt, ticket, or
appointment content. A revoked relation stops safely and clears checkpoint-only
confirmation, selected-slot, and pending-operation data; it never deletes
PostgreSQL business facts. `trace_id` is per invocation only and is not part of
thread ownership or authorisation.

Natural-language property references remain unverified text. Model output
cannot write `property_id` or `property_context_verified`.

`SERVICE_DURATION_MINUTES_BY_CATEGORY` centrally maps all three first-release
categories to 60 minutes. User-stated estimates do not override this scheduling
rule. The MCP adapter receives it as `requested_duration_minutes`. Candidate
starts remain aligned to the Task 5 30-minute granularity.

## Interrupt, resume, and message replay

The three interrupt payloads are `NEED_INFORMATION`,
`DUPLICATE_TICKET_SELECTION`, and `APPOINTMENT_SLOT_SELECTION`. Resume inputs
are a closed discriminated union. They bind to `intent_version`, and selection
resumes also bind to the current candidate fingerprint. A stale version,
fingerprint, rank, ticket, wrong kind, missing interrupt, or different property
fails safely without advancing the Graph.

LangGraph config `thread_id` is always derived from the typed external input.
Switching actor, user, actor type, or property requires a new thread.
`get_state()` exposes a sanitized view only after trusted caller validation; it
never exposes checkpoint tuples, blobs, connections, or dependencies.

Every normal user turn has a deterministic message ID based on thread, trace,
and role, so node retry and process replay cannot append it twice. A structured
slot/duplicate-selection resume is control input, not a fabricated chat message.
A `PROVIDE_INFORMATION` resume is the sole exception: it contains a real new
user message, is appended once with its own message ID, and returns to typed
interpretation without changing the durable goal merely because information was
supplied.

## Snapshot refresh and idempotency

The Graph refreshes `get_ticket_snapshot` after ticket creation, before a
confirmed booking/reschedule, after mutations, for status queries, and before
ticket escalation. If the database version differs from the checkpoint version,
the database wins and stale slot confirmation is cleared.

Mutation preparation is a separate checkpointed node. Its stable key hashes:

```text
thread_id + intent_version + action + normalized payload
+ target entity + expected ticket/appointment versions
```

It excludes trace ID, current time, retry count, and random IDs. Replaying the
same logical operation therefore reaches Application idempotency with the same
key. A tool success whose response is lost is not advertised as success; a
later replay and fresh snapshot establish the business result. General
`UNKNOWN_COMMIT` reconciliation remains Stage C work.

## Checkpoint database and lifecycle

`CHECKPOINT_DATABASE_URL` points to a separate database such as
`fixflow_checkpoints` on the same PostgreSQL server. The official
`AsyncPostgresSaver.setup()` owns its internal schema. Business Alembic does not
create, inspect, or remove these tables. The composition root initializes the
saver once, compiles the Graph, shares one initialized MCP session, and closes
all resources at shutdown. Pickle fallback and dynamic serialization modules
are disabled; checkpoint state is a validated Agent State JSON string.

For a new developer environment, after Docker PostgreSQL is healthy run:

```powershell
$env:PYTHONPATH = "backend"
uv run python -m app.agent_runtime.initialize_checkpoints
```

The command reads `CHECKPOINT_DATABASE_URL`, creates only the named checkpoint
database if absent, and invokes the official saver setup. It never copies
LangGraph internal SQL into this repository. `.env.example` provides a safe
placeholder. This explicit command is not part of an ordinary Graph run.

On Windows, psycopg asynchronous connections require a Selector event loop.
`initialize_checkpoints` configures it before `asyncio.run`; every future Agent
host entrypoint must call the same narrowly scoped function before creating its
event loop. No library module changes the global policy at import time.

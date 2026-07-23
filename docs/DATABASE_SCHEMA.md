# Database schema

## Scope and ownership

The core business schema and its Task 4 audit extension persist the deterministic
release-1 repair loop.
PostgreSQL is the source of truth for identities, authorization links, repair
tickets, workers, formal appointments, accepted worker events, status history,
and idempotency records. Pure domain objects and transition rules remain under
`backend/app/domain`; SQLAlchemy mappings under
`backend/app/infrastructure/database/models` contain no transition behavior.

## Tables

| Table | Responsibility |
| --- | --- |
| `users` | Preset resident and operator identities; only password hashes are stored |
| `properties` | Serviceable residential-unit identity and address |
| `resident_property_relations` | Resident authorization link to a property |
| `repair_tickets` | Current ticket snapshot, issue facts, severity, escalation prior, rework count, and optimistic version |
| `ticket_status_history` | Append-only accepted ticket transition evidence |
| `workers` | Maintenance-worker identity, service area, and active flag |
| `worker_skills` | Unique typed capability held by a worker |
| `worker_availability` | Non-empty `TSTZRANGE` availability declarations |
| `appointments` | Formal appointment snapshot, immutable purpose/range, terminal outcome facts, supersession, and optimistic version |
| `appointment_status_history` | Append-only accepted appointment transition evidence with request Trace identity |
| `worker_events` | Canonical accepted behavior evidence linked to its appointment, with request Trace identity, per-appointment sequence, and external identity |
| `idempotency_records` | Mutation identity, request hash, execution state, and durable result envelope |
| `policy_documents` | Versioned synthetic-policy metadata, category/topic, authority, effective interval, source/content identity, and exact embedding profile |
| `policy_chunks` | Bounded evidence text, explicit search terms, paired decision fields, and fixed `vector(384)` embedding |
| `agent_runs` | Persistent execution lifecycle, terminal marker, trusted caller context, and per-run sequence allocator |
| `agent_trace_events` | Sanitized, idempotent control/audit evidence, either associated with one Run or explicitly runless |
| `outbox_events` | Transactional domain-event delivery evidence with retry state and fenced dispatcher lease |
| `operation_reconciliation_cases` | UNKNOWN_COMMIT control state, evidence resolution, retry schedule, and fenced lease |

All core foreign keys explicitly use `ON DELETE RESTRICT`. Users, properties,
workers, tickets, appointments, histories, worker events, and idempotency rows
are retained or disabled rather than cascade-deleted.

## Typed values

Application fields use Python `StrEnum`. PostgreSQL stores every enum-shaped
field as `VARCHAR` with an explicitly named `CHECK`; no PostgreSQL native enum is
created. The first migration writes the allowed strings literally so later
application enum changes cannot rewrite migration history.

Persisted issue categories are exactly `WATER_LEAK`, `ELECTRICAL`, and
`DOOR_LOCK`. Persisted severities are `LOW`, `MEDIUM`, `HIGH`, and `EMERGENCY`.
Worker skills are `PLUMBING`, `ELECTRICAL`, and `LOCKSMITH`. The immutable
issue-to-skill mapping lives once in the pure domain layer. An uncertain LLM
classification cannot create an `UNKNOWN` ticket.

## Database-enforced constraints

The database enforces non-null fields, foreign keys, stable uniqueness, positive
versions, non-negative rework counts, non-empty time ranges, enum value sets,
and append-only history version steps. It also enforces that an `ESCALATED`
ticket has exactly one permitted prior status and that a non-escalated ticket
has no escalation prior.

A partial unique index named `uq_appointments_ticket_booked` permits at most one
`BOOKED` appointment for a ticket. The `btree_gist` extension and
`ex_appointments_worker_booked_overlap` exclusion constraint reject overlapping
`BOOKED` ranges for the same worker. Terminal historical appointments are
excluded from that predicate and therefore do not block later bookings.

`supersedes_appointment_id` is a nullable, unique, restrictive self-foreign key.
It cannot reference the new appointment itself, and one old appointment can be
referenced by at most one replacement. Same-ticket and purpose consistency are
cross-aggregate domain-service checks performed when rescheduling atomically.

Idempotency identity is unique across
`(scope, actor_type, actor_id, idempotency_key)`. `actor_id` is always a stable,
non-null string, including for a system actor, so correctness does not depend on
PostgreSQL NULL uniqueness behavior.

## Rules intentionally outside PostgreSQL

PostgreSQL does not implement the complete ticket or appointment state machine,
worker-event predecessor order, actor/reason authorization, emergency routing,
resident acceptance, rework-purpose consistency, safe escalation recovery, or
atomic mapping from domain decisions to multiple aggregates and histories.
Those rules remain deterministic domain and application-service logic.
There are no business triggers or ORM event listeners.

Worker events have a positive, unique `(appointment_id, sequence_no)` and a
unique stable external key. Those local constraints prevent duplicates, while
the strict `ACCEPTED -> DEPARTED -> ARRIVED -> STARTED -> outcome` validation
stays in the pure domain layer so rejected attempts create no canonical event.

## Migration and integration testing

Revision `20260719_0001` (`create_core_property_repair_schema`) creates `vector`
and `btree_gist` with `IF NOT EXISTS`, then creates tables in foreign-key order.
Downgrade removes only this revision's tables and indexes in reverse order; it
does not drop shared extensions.

Revision `20260719_0002` (`add_mutation_audit_links`) leaves the first migration
immutable and adds the Task 4 audit fields required for transactional use cases:
non-null `trace_id` on appointment history and Worker Event rows. Worker Events
derive their ticket through the authoritative appointment relationship instead
of duplicating `ticket_id`. Downgrade removes only the new Trace columns and
indexes.

Revision `20260720_0003` (`add_policy_retrieval_schema`) adds versioned
`policy_documents` and fixed `vector(384)` `policy_chunks`. It enforces unique
code/version and document/chunk index pairs, finite enum-shaped topic/category
values, valid timezone-aware effective intervals, paired decision key/value,
non-empty content/search terms, restrictive document foreign keys, and a named
GiST exclusion constraint preventing same-code effective-period overlap.
The exact profile fields are `embedding_provider`, `embedding_model`,
`embedding_dimension`, and `embedding_profile_version`; the dimension is fixed
to 384 and profile strings are non-blank. The exclusion range explicitly
coalesces an open end to timestamp infinity. Disabled documents remain covered
because disablement affects retrieval visibility, not historical integrity.
Upgrade ensures `vector` and `btree_gist`; downgrade removes only these policy
tables and indexes and preserves shared extensions.

Revision `20260721_0004` (`add_outbox_and_trace_runtime`) adds exactly
`agent_runs`, `agent_trace_events`, and `outbox_events`. Runs have a closed
trigger/status vocabulary, terminal-timestamp invariant, and a terminal-event
marker that must agree with the final status. A partial unique index permits at
most one lifecycle terminal Trace Event per Run. Trace events have a unique
event key; `run_id` and `sequence_number` are paired (both absent for a runless
event, otherwise a positive unique sequence within that Run). Outbox rows have
a stable unique event key, positive aggregate version, nonnegative attempts,
closed delivery state, dispatch index, retry availability, safe failure
summary, and optional run/thread correlation. A `PROCESSING` row must have
exactly `claimed_by`, a server-generated UUID `claim_token`, and
`claim_expires_at`; all three are null in every other state. Downgrade removes
only these three tables and does not alter domain, policy, or Checkpoint
storage.

Integration tests create randomly named databases with the prefix
`fixflow_migration_test_`, use the project PostgreSQL container only as the
administrative server, and always terminate remaining test connections before
dropping each database. They verify `upgrade -> downgrade -> upgrade`, metadata,
typed checks, restrictive foreign keys, Task 4 audit columns, appointment
uniqueness and overlap,
supersession, idempotency uniqueness, timezone-aware fields, and ORM relationship
loading. The normal `fixflow` development database is not migrated or cleared by
these tests.

## Separate and deferred storage

Revision `20260722_0005` adds a nullable unique `operation_id` to legacy
idempotency rows (new mutations populate it), adds the reconciliation case
table, and extends the Trace source vocabulary with `RECONCILIATION`. Its
downgrade removes only these additions and restores the prior Trace check. The
revision expands `agent_trace_events.source` from `VARCHAR(7)` to `VARCHAR(14)`;
the downgrade removes reconciliation-only audit rows before restoring the old
width because revision 0004 cannot represent that source.

Official LangGraph Checkpoint tables remain in the isolated checkpoint
database and are never managed by business Alembic. Task 10 now supplies Trace
and Outbox storage; conversations, Replay scenarios, fault-harness records, and
evaluation storage remain deferred and require separately reviewed migrations
only if their later design truly needs persistence.

Revision `20260723_0006` adds the deterministic Replay control plane:

- `agent_replay_bundles`, one RESTRICT-linked artifact per `agent_runs` row;
- `agent_replay_steps`, unique by Bundle/sequence and Bundle/step key;
- `agent_replay_executions`, unique by requesting actor and request-key
  fingerprint.

All status and step fields remain `VARCHAR + named CHECK`, not PostgreSQL
Native Enum. READY Bundles require complete expected projections and checksum.
RUNNING Executions cannot have a completion result; terminal Executions require
one. Foreign keys use `ON DELETE RESTRICT`, timestamps are timezone-aware, and
downgrade removes Replay rows/tables before removing `REPLAY` from the Trace
source check. The isolated LangGraph Checkpoint database contains no Replay

Task 13 adds no business table and no migration. Prompt assets remain
Git-managed files; provider metadata uses the existing closed Trace and Replay
projections rather than a prompt or LLM-response table.
tables.

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

Integration tests create randomly named databases with the prefix
`fixflow_migration_test_`, use the project PostgreSQL container only as the
administrative server, and always terminate remaining test connections before
dropping each database. They verify `upgrade -> downgrade -> upgrade`, metadata,
typed checks, restrictive foreign keys, Task 4 audit columns, appointment
uniqueness and overlap,
supersession, idempotency uniqueness, timezone-aware fields, and ORM relationship
loading. The normal `fixflow` development database is not migrated or cleared by
these tests.

## Deferred tables

This revision deliberately does not create conversations, Agent checkpoints,
Trace runs/events, Outbox/dead-letter records, policy documents/chunks/embeddings,
or any frontend and evaluation storage. Policy documents/chunks are now supplied
by revision `20260720_0003`; the remaining deferred tables require separately
reviewed future migrations.

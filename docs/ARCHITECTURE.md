# Architecture

## System boundary

```mermaid
flowchart LR
    Resident["Resident UI - later"] --> API["FastAPI Online API"]
    Operator["Operator workbench - later"] --> API
    API --> Orchestrator["Single typed orchestrator - later"]
    Orchestrator --> Services["Deterministic application services"]
    Orchestrator --> LLM["Interpret and compose only - later"]
    Orchestrator --> MCPClient["MCP client - later"]
    MCPClient --> MCP["property-operations-mcp"]
    MCP --> Services
    Services --> PostgreSQL[("PostgreSQL + pgvector")]
```

The API, future orchestrator, and MCP handlers depend on application-service
interfaces. Application services own transactions and call domain/repository
ports. LLM and workflow code never opens ORM transactions.

The MCP server is a separate process. Online and fake MCP implementations must
use the same Pydantic request and result schemas.

## Source-of-truth rules

- PostgreSQL owns users, properties, authorization relations, tickets,
  appointments, worker data, immutable worker events, versions, and history.
- `workflow_stage` is orchestration progress, not domain truth.
- A workflow resume must reread current ticket and appointment snapshots and
  compare versions before continuing.
- Candidate slots are transient workflow data until the resident confirms one;
  only then is a formal appointment created.
- Core entities use relational columns. JSONB is limited to trace payloads,
  structured model snapshots, tool request/results, fault configuration, and
  state-delta details.

## Proposed week-1 data model

This is the frozen, user-approved state-design proposal. It is not an implemented
schema; implementation starts only under the separately authorized Task 2.

| Aggregate/table | Key responsibilities and constraints |
| --- | --- |
| `users` | Preset resident/operator identities and active status |
| `properties` | Property identity, building/unit, and service area |
| `resident_property_relations` | Authorized resident-property relation with FKs and uniqueness |
| `repair_tickets` | Issue fields, proposed ticket status, resident/property FKs, version, rework count, and current escalation prior status |
| `ticket_status_history` | Every accepted status change with actor and trace identifiers |
| `ticket_events` | Append-only scheduling, rework, acceptance, and conflict evidence |
| `workers` | Active flag, service area, and stable identity |
| `worker_skills` | Worker-to-supported-issue-category relation |
| `worker_availability` | Time windows used by deterministic matching |
| `appointments` | Immutable time interval, proposed status, ticket/worker FKs, version, required terminal-outcome actor/reason/evidence/timestamp data, and supersession link |
| `appointment_history` | Every accepted transition with actor, trace, and version data |
| `worker_events` | Canonical append-only behavior linked to ticket/appointment, subject worker, real recording actor, sequence, and source idempotency key |
| `idempotency_records` | Unique operation scope/key, request hash, result reference, and status |

Future-phase tables such as conversations, checkpoints, policies, Trace,
Outbox, and dead letters are documented in the project specification but are
not created during stage 0.

## Concurrency and transaction boundary

- Ticket and appointment mutations use optimistic versions.
- Active worker appointments use a PostgreSQL range exclusion constraint to
  prevent overlaps even under concurrent requests.
- A partial unique index allows at most one active `BOOKED` appointment per
  ticket. Appointment interval and worker fields are immutable after creation.
- Idempotency keys have a database unique constraint and are scoped to the
  business operation/actor as defined during week 1.
- A service commits the business mutation and history in one transaction.
- In phase 3, externally observed events also write an Outbox record in that
  transaction.

The proposed storage strategy is a Python string Enum paired with a PostgreSQL
string column and named `CHECK` constraint, rather than PostgreSQL Native Enum.
The approved transition rules will live in the domain layer; API, MCP, workflow,
and ORM adapters never set statuses directly.

For generic appointment `CANCELLED`/`NO_SHOW`, the database and domain schema
retain `actor_type`, `actor_id`, `reason_code`, `reason_text`, `evidence`, and
`occurred_at`. Reason codes distinguish resident/worker no-show and resident,
worker, worker-rejection, operator, and system cancellation without expanding
the appointment-status enum. Terminal appointments never reactivate; later work
creates a new row and audit corrections are append-only.

## Error taxonomy

Future implementation separates:

- domain errors: illegal transition, invariant violation, overlap;
- application errors: permission denial, idempotency conflict, stale version;
- infrastructure errors: database/MCP/network unavailable or malformed response.

## Approved dependency direction

```text
API / MCP / future workflow
          -> application services
          -> domain model and repository protocols
          -> infrastructure adapters
```

Infrastructure imports domain/application contracts; domain code does not
import FastAPI, MCP, LangGraph, or SQLAlchemy sessions.

## Stage-0 decisions

- Repository root: `F:\agent\fixflow`.
- Python: exactly 3.12.
- Dependency management: uv, committed `uv.lock`, no parallel dependency files.
- PostgreSQL image line: pgvector-enabled PostgreSQL 16 for local development.
- The approved final enums and matrices are in `docs/STATE_MACHINE.md` and remain
  unimplemented pending the user's explicit Task 2 instruction.

# FixFlow implementation roadmap

## Project north star

**FixFlow: a recoverable and auditable property-maintenance coordination Agent.**

The central engineering question is:

> How can a resident's unstructured, changing, multi-turn repair request be
> converted into constrained business state and then advanced reliably across
> the resident, property operator, and maintenance worker over a long-running
> task?

FixFlow is a portfolio and interview project for Agent application engineering.
It must finish as an Agent product with a complete business loop, not drift into
a generic property backend, a chat-only bot, a basic RAG demo, a general Agent
platform, or a happy-path-only showcase.

The final system must demonstrate orchestration, natural-language understanding,
typed state, task memory, policy RAG, MCP tools, deterministic business services,
authorization and transactions, idempotency and optimistic locking, business
Trace, fault injection and recovery, end-to-end evaluation, a comparable ReAct
baseline and ablations, two user-facing surfaces, and reproducible Docker Compose
delivery.

## Frozen release-1 scope

Persisted repair tickets support exactly:

```text
WATER_LEAK
ELECTRICAL
DOOR_LOCK
```

An uncertain classification is clarified or escalated; it is not persisted as
an `UNKNOWN` ticket. The product surfaces are a resident Agent chat page and a
property-operator workbench. Worker actions are simulated transparently through
the operator workbench; release 1 has no independent worker frontend.

## Core architecture

```text
Single Orchestrator
+ Typed State Graph
+ a small number of LLM Nodes
+ Deterministic Application Services
```

The following boundaries are non-negotiable unless the user explicitly approves
a replacement:

- PostgreSQL is the sole source of business truth.
- A LangGraph checkpoint stores conversation work state, not authoritative
  ticket or appointment state; every resume rereads current database snapshots.
- LangGraph nodes, API controllers, and MCP handlers do not own ORM transactions.
- The MCP server runs separately from FastAPI and the Agent runtime and calls
  application services through typed contracts.
- LLMs interpret language and compose evidence-grounded responses. They do not
  decide authorization, legal transitions, time conflicts, worker eligibility,
  closure conditions, policy validity, or mutation retry safety.
- Deterministic code and PostgreSQL constraints establish business correctness.
- Multiple freely collaborating Agents are not the default architecture.

## Decision priority

When requirements or implementation details appear to conflict, use this order:

1. design decisions explicitly approved later by the user;
2. currently approved and frozen repository documents;
3. constraints embodied by committed migrations, domain rules, and tests;
4. the original project specification's goal, scope, and final-delivery requirements;
5. local implementation suggestions in an individual task prompt.

The original specification protects the product goal and complete delivery
scope. Later frozen documents may replace its early candidate implementation
details. A local task may not silently change the product, scope, or architecture.
When code and documents disagree, do not assume the code is correct: stop,
identify the conflict, and request a decision.

## Delivery stages

### Stage A: deterministic business core

Status at roadmap creation:

```text
Completed:
- project initialization and reproducible Python/PostgreSQL environment
- approved domain-state design
- pure domain state model and transition tests
- PostgreSQL ORM mappings and first business migration

Implemented for Task 4 review:
- focused Repository ports and SQLAlchemy implementations
- Unit of Work and explicit ORM/domain mapping
- deterministic application services
- transaction, authorization, idempotency, optimistic-lock, history, and concurrency tests

Next:
- independent MCP server
- MCP contract tests
```

Acceptance requires a complete ticket and appointment path without an LLM,
rejection of illegal transitions and unauthorized writes, idempotent mutations,
database-safe concurrent booking, and MCP handlers that only call application
services.

### Stage B: Agent, RAG, and product main flow

Required work:

- FastAPI business endpoints, simple JWT, and preset resident/operator accounts;
- a LangGraph orchestrator, typed Agent State, and `intent_version` invalidation;
- `interpret_message`, `compose_response`, one real domestic-model
  `LLMProvider`, and a contract-compatible Fake LLM;
- policy RAG with effective-time filtering, conflict detection, and escalation
  for insufficient evidence;
- MCP client, resident chat, basic operator workbench, and SSE.

Acceptance requires all three issue categories to start from natural language,
multi-turn missing-field collection, invalidation of stale plans after user
correction, deterministic enforcement around LLM output, evidence-linked
actions, exclusion of expired policies, and resident/operator permission
separation.

### Stage C: reliability, Trace, and Engineering Harness

Required work:

- Transactional Outbox and worker;
- `UNKNOWN_COMMIT`, post-commit timeout reconciliation, and recovery from a crash
  before checkpoint advancement;
- project-owned business Trace and embedded Trace timeline;
- Fake MCP, Fake Clock, Fault Injector, Scenario Loader, Replay, Dead Letter, and
  explicit state-conflict handling;
- at least ten fault scenarios and coverage of the original F01-F12 set.

F01 (commit succeeded but create call timed out) and F03 (crash after tool commit
but before checkpoint) must cross a real PostgreSQL commit and recovery boundary;
a fake error string is not evidence.

### Stage D: evaluation, comparison, and final delivery

Required work:

- approximately 100 scenarios and at least approximately 30 frozen evaluation cases;
- deterministic business oracles;
- a ReAct baseline using the same model, tools, data, and retry budget;
- ablations without `intent_version`, policy effective-time filtering, and
  post-timeout state reconciliation;
- metric reports, at least two real bug retrospectives, complete Docker Compose,
  final README, demo script, and reproducible resume metrics.

The project is not complete after Stage A or Stage B.

## Mandatory capability register

Status values are `implemented`, `partial`, and `deferred`. A deferred capability
is still required. No row may be removed or weakened without explicit user approval.

| Capability | Target stage | Current status | Corresponding file / commit | Acceptance evidence | Deletable? |
| --- | --- | --- | --- | --- | --- |
| Resident natural-language repair request | B | deferred | `docs/PROJECT_SPEC.md` | Three categories start from natural language | No |
| Multi-turn information completion | B | deferred | `docs/PROJECT_SPEC.md` | Missing fields are requested and validated | No |
| User correction and `intent_version` | B | deferred | this roadmap | Old analysis, slots, confirmations, and pending actions become invalid | No |
| Exact structured open-ticket candidate query | A | implemented | `backend/app/application/ticket_service.py`, `backend/app/infrastructure/database/repositories/ticket.py`, Task 4 review | Exact resident, property, category, and normalized-location filtering plus explicit operator override pass; semantic duplicate detection remains deferred | No |
| Policy RAG | B | deferred | `docs/PROJECT_SPEC.md` | Hybrid retrieval returns attributable evidence | No |
| Policy effective-time validation | B | deferred | this roadmap | Expired policy misuse rate is zero in frozen cases | No |
| Worker skill matching | A | implemented | `backend/app/domain/enums.py`, `backend/app/infrastructure/database/repositories/appointment.py`, Task 4 review | Formal booking validates the typed mapping against active worker skill and availability | No |
| Candidate-slot ordering | A | deferred | original project specification | Earliest time, workload, area, then stable ID | No |
| Booking and rescheduling | A | implemented | `backend/app/application/appointment_service.py`, Task 4 review | Atomic booking/rescheduling and PostgreSQL concurrency tests pass | No |
| Worker events | A | implemented | `backend/app/application/worker_event_service.py`, Task 4 review | Strict sequence, source replay, distinct-key concurrency, and atomic cross-entity effects pass against PostgreSQL | No |
| Resident acceptance and rework | A | implemented | `backend/app/application/ticket_service.py`, Task 4 review | Acceptance closes; concurrent rejection increments once; rejected or cancelled rework booking preserves the same ticket | No |
| Human escalation | A/B | implemented | `backend/app/application/ticket_service.py`, Task 4 review | Safe operator recovery derives its target and cannot substitute resident acceptance | No |
| Resident/worker state conflict | C | deferred | original F06 scenario | Automation stops and operator review is required | No |
| Independent MCP server | A | deferred | `docs/MCP_CONTRACTS.md` | Streamable HTTP contract tests call application services | No |
| LangGraph orchestrator | B | deferred | `docs/ARCHITECTURE.md` | Typed graph resumes from fresh domain snapshots | No |
| Three-layer task memory | B | deferred | original project specification | Checkpoint work state never replaces PostgreSQL facts | No |
| Business Trace | C | deferred | original project specification | Complete, replayable timeline explains retries and recovery | No |
| Transactional Outbox | C | deferred | `docs/ARCHITECTURE.md` | Business write and event commit atomically | No |
| `UNKNOWN_COMMIT` recovery | C | deferred | `docs/STATE_MACHINE.md` | Real post-commit timeout is reconciled without duplication | No |
| Fault injection | C | deferred | original F01-F12 scenarios | Configured faults produce verified recovery behavior | No |
| Replay | C | deferred | original project specification | Stored Trace/scenario can be deterministically replayed | No |
| Evaluation suite | D | deferred | original project specification | Approx. 100 scenarios and frozen deterministic report | No |
| ReAct baseline | D | deferred | original project specification | Same-model/tool/data comparison is reproducible | No |
| Core ablations | D | deferred | this roadmap | Three approved removals produce comparable metrics | No |
| Resident frontend | B | deferred | `docs/PROJECT_SPEC.md` | Chat, status, slots, cancellation, and acceptance work | No |
| Operator workbench | B/C | deferred | `docs/PROJECT_SPEC.md` | Ticket operations, evidence, conflicts, and Trace timeline work | No |
| Docker Compose delivery | A/D | partial | `docker-compose.yml`, `448f9e2` | PostgreSQL works now; final one-command system remains | No |

## Deferred but mandatory features

The following are not implemented yet and are not cancelled:

- semantic duplicate-ticket detection;
- deterministic worker candidate ordering;
- resident appointment cancellation and ticket cancellation;
- ticket progress queries;
- resident/worker state-conflict handling;
- `intent_version` and conversation checkpoints;
- policy RAG;
- MCP client and independent server;
- LangGraph and an LLM provider;
- business Trace;
- Outbox and `UNKNOWN_COMMIT` recovery;
- fault injection, Replay, and the Engineering Harness;
- resident and operator frontends;
- evaluation, ReAct baseline, and ablations.

Tasks that do not implement these items must leave them visible here. Long delay
is not permission to remove them.

## Task-alignment gates

Every task must run the start and completion alignment checks defined in
`AGENTS.md`. A task report must connect local work to this roadmap rather than
reporting only that code passed tests. Any proposed change to the north star,
release scope, product surfaces, architecture boundary, technology commitments,
or mandatory capability register requires explicit user approval before work.

## Task 4 alignment statement

Task 4 does not directly implement an Agent. It supplies the reliable,
deterministic, transactionally executable business capabilities that the future
Agent and MCP server will call.

Task 4 advances authorization, repositories, Unit of Work, application services,
idempotency, optimistic locking, status history, concurrent transactions, and
the deterministic boundary exposed later to MCP/Agent code. It must not become a
generic enterprise backend framework, ticket platform, event-sourcing system,
Saga framework, idempotency platform, or repository library. After Task 4, the
next Stage-A priority remains the independent MCP server, not additional generic
infrastructure abstraction.

## Roadmap maintenance

At task completion, update statuses and evidence in this document when a roadmap
capability materially changes. Documentation-only wording changes do not justify
claiming a capability as implemented. A capability becomes `implemented` only
when its task acceptance evidence exists and relevant tests pass.

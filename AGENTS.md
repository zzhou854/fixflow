# FixFlow development instructions

This file applies to the entire repository.

## Authority and scope

- `docs/PROJECT_SPEC.md` records the frozen product scope.
- `docs/ARCHITECTURE.md` records component and data-ownership boundaries.
- `docs/STATE_MACHINE.md` records the user-approved domain states and transitions.
- `docs/MCP_CONTRACTS.md` owns MCP transport and tool-contract rules.
- `docs/IMPLEMENTATION_ROADMAP.md` preserves the north star, delivery stages,
  mandatory capabilities, and alignment gates.
- Do not silently broaden the three supported issue categories: water leak,
  electrical fault, and door-lock fault.

When instructions conflict, apply this priority:

1. later design decisions explicitly approved by the user;
2. currently approved and frozen repository documents;
3. committed migrations, domain rules, and tests;
4. the original project's goal, scope, and final-delivery requirements;
5. local implementation suggestions in one task prompt.

The original specification protects the final product goal. Later frozen
documents may replace early implementation candidates. A task cannot silently
change the goal, product surfaces, release scope, or core architecture. If code
and documents disagree, stop and report the conflict instead of assuming the
code is authoritative.

## Delivery discipline

1. Read the applicable documents and existing implementation before editing.
2. State the smallest task goal and the minimum required change.
3. Keep domain rules out of API controllers, LangGraph nodes, and MCP handlers.
4. Run formatting, type checks, and task-relevant tests.
5. Do not claim completion while required checks fail.
6. Report changed files, design reasons, commands, results, remaining issues,
   and the next recommended task.

Do not generate broad placeholder trees, `pass`-only modules, fake online
services, or unapproved future-week features.

## Mandatory task alignment

Before implementation begins, report:

1. which roadmap capabilities the task advances;
2. which capabilities the task explicitly does not implement;
3. whether the task changes the business scope;
4. whether the task changes an architecture boundary;
5. whether it introduces a new technology;
6. whether it delays an existing roadmap item;
7. whether it conflicts with a frozen document or committed constraint.

If the goal, scope, product shape, architecture, technology commitment, or a
mandatory capability would change, stop and wait for explicit approval.

After implementation, report:

1. which roadmap items were completed or advanced;
2. the tests or other evidence that prove this;
3. which capabilities remain deferred;
4. whether any original requirement was removed, weakened, or substituted;
5. any new technical debt;
6. effects on later Agent, RAG, Trace, Harness, or evaluation work;
7. whether `docs/IMPLEMENTATION_ROADMAP.md` was synchronized.

Passing tests alone is not a complete task report. Every report must explain how
the change serves the final Agent product.

## Architecture rules

- Use one orchestrator, typed workflow state, few LLM nodes, and deterministic services.
- PostgreSQL is the only source of business truth.
- A LangGraph checkpoint never overrides current ticket or appointment state.
- LLM output must pass Pydantic validation before entering workflow state.
- LangGraph nodes and MCP handlers do not manage ORM transactions directly.
- Mutating operations require actor, trace, idempotency, and expected-version data.
- Worker behavior is append-only event data; it does not replace ticket or appointment state.
- Prefer the simplest testable design; add no middleware merely to showcase technology.

## Backend expansion guard

- Do not create generic CRUD, workflow, repository, event-sourcing, Saga, or
  idempotency frameworks.
- Do not introduce a complex dependency-injection container.
- Do not introduce Redis, Kafka, Celery, Kubernetes, unnecessary microservices,
  or another business database.
- Do not spend project time on administrative features unrelated to the frozen
  demonstration loop.
- Do not create tables, modules, or abstractions for hypothetical future needs.
- Every infrastructure design must directly support a frozen fault scenario,
  Agent flow, or evaluation goal.

If backend work cannot be tied to at least one of business-loop completion,
authorization, state consistency, idempotency, concurrency, recovery, Trace,
fault injection, or evaluation, stop for scope review.

## Tooling

- Python is fixed to 3.12.
- Use `uv`, `pyproject.toml`, and committed `uv.lock` only.
- Install with `uv sync`; test with `uv run pytest`.
- Do not introduce Poetry, PDM, or a manually maintained `requirements.txt`.
- Online code uses PostgreSQL, not Redis, MySQL, MongoDB, or a separate vector store.

## Current phase

Stage A is complete: the domain and persistence model, transactional Application
services, deterministic scheduling, and the independent MCP server are committed.
Task 6 typed Agent State and Task 7 auditable policy retrieval are committed.
The current approved boundary is Task 8: one LangGraph orchestrator, the formal
MCP client, isolated PostgreSQL checkpoints, Interrupt/Resume, fresh snapshots,
stable idempotency, and the first natural-language repair/booking flow. Task 8
does not authorize online providers, JWT/API, Trace, Outbox, frontend, or Stage C.

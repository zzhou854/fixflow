# FixFlow

FixFlow is a recoverable and auditable property-maintenance coordination agent.
It converts a resident's changing, multi-turn repair request into constrained
business state and reliably advances a long-running task across the resident,
property operator, and maintenance worker.

The first release is deliberately limited to water leaks, electrical faults,
and door-lock faults. It is not a general agent platform or a form-filling chat
demo.

## Current status

Stage A, Stage B, and Tasks 9–11 are committed. Task 12 is at its code-review gate:

- Python 3.12 and uv project configuration;
- a FastAPI/JWT boundary with role-separated resident and operator APIs;
- PostgreSQL with pgvector through Docker Compose, validated healthy;
- frozen domain states and pure transition rules;
- SQLAlchemy persistence mappings and three reviewable Alembic migrations;
- PostgreSQL constraints and disposable-database integration tests;
- focused Repository ports and SQLAlchemy implementations;
- explicit ORM/domain mapping and Unit of Work;
- deterministic transactional services for tickets, appointments, Worker Events,
  acceptance/rework, and escalation/recovery;
- request idempotency, optimistic locking, status history, authorization, and
  real PostgreSQL concurrency tests;
- deterministic 30-minute candidate-slot queries with hard skill, activity,
  service-area, availability, and booking-conflict filters;
- an independent `property-operations-mcp` process exposing eight typed business
  tools plus the trusted read-only `get_operation_outcome` tool;
- Pydantic contract, handler, real PostgreSQL tool, and real MCP client transport
  tests;
- strict JSON-serializable Agent State with separate utterance/task intents,
  typed user availability and non-guaranteed candidate slots;
- deterministic missing-field computation, `intent_version` invalidation,
  safety-review routing boundary, and explicit timezone context;
- provider-neutral LLM contracts, versioned prompts, language-only interpret and
  compose nodes, and a scripted test provider;
- synthetic, versioned policy documents and 384-dimensional pgvector chunks;
- SQL-first category/topic/effective-time filtering, deterministic hybrid
  retrieval, attributable evidence, conflict and sufficiency decisions;
- a 21-case frozen policy retrieval evaluation without an LLM judge;
- a single deterministic LangGraph orchestrator with typed Interrupt/Resume;
- a production Streamable HTTP MCP client using the shared business contracts;
- an isolated official PostgreSQL checkpointer and fresh-snapshot recovery;
- the natural-language create-ticket and book-appointment main flow;
- authenticated Agent thread and strict Resume APIs with bounded SSE delivery;
- an idempotent Argon2-backed development seed;
- a React/TypeScript/Ant Design resident chat and operator workbench;
- real API-to-Agent-to-MCP-to-PostgreSQL vertical and cross-resident tests;
- a same-transaction domain Outbox with stable event identities;
- a leased at-least-once Dispatcher, retry/backoff, and dead-letter state;
- persistent sanitized Agent/API/MCP/domain Trace runs and events;
- a ticket-linked operator execution timeline kept separate from business history;
- durable UNKNOWN_COMMIT cases, fenced claims, and authoritative operation evidence;
- a deterministic test-only fault-injection port;
- strict, checksummed Replay Bundles and typed external-result tapes;
- tape-only deterministic execution over the existing Single Orchestrator;
- an Operator-only, ticket-linked Recovery Console with read-only recommendations;
- the implementation roadmap and mandatory task-alignment gates.

GLM-5.1 structured interpretation is available behind explicit configuration.
Online embedding, online free-text generation, and Stage D evaluation remain
mandatory roadmap work, not cancelled scope. Replay verifies control-plane determinism;
it is not Event Sourcing, database time travel, or automatic repair.

## Prerequisites

- Python 3.12
- [uv](https://docs.astral.sh/uv/)
- Docker with Docker Compose

## Standard commands

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
docker compose up -d postgres
uv run alembic upgrade head
$env:PYTHONPATH = "backend"; uv run python -m app.api.run
uv run python -m mcp_server
$env:PYTHONPATH = "backend"; uv run python -m app.agent_runtime.initialize_checkpoints
uv run python -m app.dev_seed
uv run python -m app.outbox.run
uv run uvicorn app.main:app --app-dir backend --reload
cd frontend && npm ci && npm run dev
```

The MCP command starts the independent `property-operations-mcp` service at
`http://127.0.0.1:8765/mcp` by default. Configure `MCP_HOST`, `MCP_PORT`, and
`DATABASE_URL` through the environment. The service uses Streamable HTTP and
does not run inside the FastAPI process.

The checkpoint-initialization command is a one-time local development step once
Docker PostgreSQL is healthy. It uses `CHECKPOINT_DATABASE_URL` to create the
isolated checkpoint database if needed and lets the official LangGraph saver
create its four internal tables; business Alembic never manages those tables.
On Windows this command sets the required Selector event-loop policy before the
event loop starts. Future Agent-host entrypoints use the same explicit setup.

For a full local demonstration, start PostgreSQL, apply business migrations,
initialise checkpoints, run the idempotent seed, then start MCP, API, and
frontend in separate terminals. The seed creates `resident_demo` /
`ResidentDemo!2026` and `operator_demo` / `OperatorDemo!2026`; these are
fictitious local-only accounts. Put a strong local `FIXFLOW_JWT_SECRET` in the ignored
`.env`. The UI clearly displays demo-runtime mode.

Browser live updates use authenticated Fetch Streaming with a Bearer header;
JWTs never enter SSE URLs. HTTP mutation responses and Thread State carry the
formal result, while bounded in-memory SSE is non-replayable. Task 9 requires
`Idempotency-Key` on thread creation, messages, Resume, and operator escalation.
Operator thread review and Trace are read-only and limited to threads linked to
a database-verified ticket. Task 10 supplies persistent Outbox and Trace. Task 11
adds UNKNOWN_COMMIT reconciliation for three Resident Agent mutations plus one
Operator-only escalation mutation, fenced workers, safe resident and
operator projections, and a test-only fault harness. Task 12 adds isolated
deterministic Replay and the read-only Recovery Console; it never reissues
business mutations or writes the formal Checkpoint.

Copy `.env.example` to the ignored `.env` file, set a local PostgreSQL password,
and place the same password in `FIXFLOW_DATABASE_URL` before starting PostgreSQL.
Do not commit `.env`. `uv.lock` is the committed dependency lock file and is the
only source for reproducible Python installation. A `requirements.txt`, if ever
required by a deployment target, must be generated from the uv-managed project
and never maintained manually.

## Documentation

- [Project specification](docs/PROJECT_SPEC.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Approved state machines](docs/STATE_MACHINE.md)
- [MCP contracts](docs/MCP_CONTRACTS.md)
- [Typed Agent core](docs/AGENT_STATE.md)
- [Policy RAG](docs/POLICY_RAG.md)
- [Implementation roadmap and alignment gates](docs/IMPLEMENTATION_ROADMAP.md)
- [FastAPI and JWT contracts](docs/API.md)
- [Transactional Outbox](docs/OUTBOX.md)
- [Persistent Trace Runtime](docs/TRACE_RUNTIME.md)
- [Resident and operator frontend](docs/FRONTEND.md)
- [Deterministic Replay](docs/REPLAY.md)
- [Recovery Console](docs/RECOVERY_CONSOLE.md)
- [Structured LLM Provider](docs/LLM_PROVIDER.md)
- [Prompt Library](docs/PROMPT_LIBRARY.md)

## Week-1 delivery order

Each item is implemented and accepted separately; the next item starts only
after relevant tests pass.

1. Database engine, session lifecycle, pgvector extension, and migration smoke test.
2. Users, properties, and resident-property relations with authorization tests.
3. Repair-ticket schema, approved status matrix, and domain transition tests.
4. Workers, skills, service areas, and availability windows.
5. Appointments, versioning, and PostgreSQL overlap exclusion constraints.
6. Idempotency records and duplicate-request behavior.
7. Small application services for ticket and appointment use cases.
8. Independent MCP tools that call those application services.
9. API, repository integration, MCP contract, and deterministic end-to-end tests.

## License and policy data

No license has been selected yet. Policy data added later must be public and
legally accessible or be clearly labeled as demonstration policy. It does not
represent the final rules of a real property-management company.

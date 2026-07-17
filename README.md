# FixFlow

FixFlow is a recoverable and auditable property-maintenance coordination agent.
It converts a resident's changing, multi-turn repair request into constrained
business state and reliably advances a long-running task across the resident,
property operator, and maintenance worker.

The first release is deliberately limited to water leaks, electrical faults,
and door-lock faults. It is not a general agent platform or a form-filling chat
demo.

## Current status

Stage 0 establishes the week-1 engineering boundary only:

- Python 3.12 and uv project configuration;
- a minimal FastAPI health endpoint;
- an initialized asynchronous Alembic environment;
- PostgreSQL with pgvector through Docker Compose;
- candidate domain-state matrices for approval;
- architecture and MCP contract documentation.

No LangGraph workflow, LLM, RAG, JWT, frontend, Outbox worker, Trace UI, or
fault-injection implementation exists yet.

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
uv run uvicorn app.main:app --app-dir backend --reload
```

Copy `.env.example` to the ignored `.env` file, set a local PostgreSQL password,
and place the same password in `FIXFLOW_DATABASE_URL` before starting PostgreSQL.
Do not commit `.env`. `uv.lock` is the committed dependency lock file and is the
only source for reproducible Python installation. A `requirements.txt`, if ever
required by a deployment target, must be generated from the uv-managed project
and never maintained manually.

## Documentation

- [Project specification](docs/PROJECT_SPEC.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Candidate state machines](docs/STATE_MACHINE.md)
- [MCP contracts](docs/MCP_CONTRACTS.md)

## Week-1 delivery order

Each item is implemented and accepted separately; the next item starts only
after relevant tests pass.

1. Database engine, session lifecycle, pgvector extension, and migration smoke test.
2. Users, properties, and resident-property relations with authorization tests.
3. Repair-ticket schema, candidate status matrix approval, and domain transition tests.
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

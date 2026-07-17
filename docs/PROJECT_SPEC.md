# FixFlow project specification

## Product objective

FixFlow turns an unstructured and changing resident repair request into a
constrained business workflow and reliably coordinates a long-running repair
across a resident, property operator, and maintenance worker.

The project must demonstrate a complete business loop, real failure handling,
diagnosis, recovery, regression verification, auditable tool use, and
reproducible evaluation. It is not a general agent framework or a chat-based
repair form.

## Frozen release-1 scope

Supported issue categories:

1. water leaks;
2. electrical faults;
3. door-lock faults.

User surfaces planned for later phases:

- resident agent chat;
- property-operator workbench.

Worker actions are simulated through the operator workbench. There is no
worker frontend in release 1.

The primary loop is:

```text
resident message -> property authorization -> interpretation and safety triage
-> missing-information collection -> policy and duplicate checks -> ticket creation
-> worker/slot matching -> resident confirmation -> booking -> worker-event tracking
-> resident acceptance -> close, rework, or human escalation
```

The flow must handle user corrections, rescheduling, cancellation, worker
rejection/cancellation, risk escalation, arrival conflicts, and rejected repair
acceptance.

## Architecture commitment

Use one orchestrator, typed workflow state, a small number of LLM nodes, and
deterministic application/domain services. Multiple freely collaborating agents
are not part of the main architecture.

LLMs may interpret language and compose evidence-grounded responses. They do
not decide permissions, legal state transitions, duplicate creation, time
conflicts, worker eligibility, policy validity, retries of mutating tools,
ticket closure, or rework eligibility.

PostgreSQL is the sole source of business truth. Workflow checkpoints store
conversation work state but cannot replace current domain snapshots.

## Technology commitment

- Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x Async, Alembic;
- PostgreSQL, pgvector, JSONB for trace/tool snapshots, and PostgreSQL FTS;
- pytest, pytest-asyncio, httpx, structlog, Pydantic Settings;
- official Python MCP SDK with a separate Streamable HTTP server;
- later: LangGraph, React, TypeScript, Vite, and Ant Design;
- uv with `pyproject.toml` and committed `uv.lock` is the only dependency source.

## Explicit non-goals

Do not add image/OCR/voice processing, payments, quotations, insurance, maps,
IoT, SMS/phone, multiple property companies, an external worker marketplace,
model fine-tuning, local-model hosting, Redis, Kafka, Celery, Kubernetes, Java
or Go services, multiple databases, a separate vector database, free-form
multi-agent group chat, a worker frontend, or complex microservices.

## Four delivery phases

1. Deterministic domain loop, PostgreSQL constraints, idempotency, MCP, and tests.
2. Typed LangGraph workflow, LLM provider, policy RAG, JWT, SSE, and two UIs.
3. Outbox, reconciliation, UNKNOWN_COMMIT recovery, Trace, harness, and faults.
4. Scenario evaluation, frozen set, ReAct baseline, ablations, reports, and handoff.

## Stage-0 acceptance

Stage 0 creates only the minimum week-1 skeleton, uv configuration, PostgreSQL
Compose service, Alembic environment, smoke test, architecture documents, week-1
sequence, and candidate domain-state matrices. Domain enums are not implemented
until the matrices are approved.


# FixFlow development instructions

This file applies to the entire repository.

## Authority and scope

- `docs/PROJECT_SPEC.md` records the frozen product scope.
- `docs/ARCHITECTURE.md` records component and data-ownership boundaries.
- `docs/STATE_MACHINE.md` contains candidate matrices only until the user approves them.
- `docs/MCP_CONTRACTS.md` owns MCP transport and tool-contract rules.
- Do not silently broaden the three supported issue categories: water leak,
  electrical fault, and door-lock fault.

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

## Architecture rules

- Use one orchestrator, typed workflow state, few LLM nodes, and deterministic services.
- PostgreSQL is the only source of business truth.
- A LangGraph checkpoint never overrides current ticket or appointment state.
- LLM output must pass Pydantic validation before entering workflow state.
- LangGraph nodes and MCP handlers do not manage ORM transactions directly.
- Mutating operations require actor, trace, idempotency, and expected-version data.
- Worker behavior is append-only event data; it does not replace ticket or appointment state.
- Prefer the simplest testable design; add no middleware merely to showcase technology.

## Tooling

- Python is fixed to 3.12.
- Use `uv`, `pyproject.toml`, and committed `uv.lock` only.
- Install with `uv sync`; test with `uv run pytest`.
- Do not introduce Poetry, PDM, or a manually maintained `requirements.txt`.
- Online code uses PostgreSQL, not Redis, MySQL, MongoDB, or a separate vector store.

## Current phase

Stage 0 may contain environment configuration, documentation, Alembic setup,
and a smoke endpoint/test. Do not implement LangGraph, LLM, RAG, React, JWT,
Outbox, workers, Trace UI, or fault injection until the corresponding phase is
explicitly started.


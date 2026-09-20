# Product acceptance evidence — 2026-09-09

## Decision

FixFlow is a stable **local demonstration candidate** for the three frozen
repair categories. It is not a production-activated online-model release.
DeepSeek V4 Flash remains limited to authenticated development allowlisting;
the repository default remains `scripted`.

## Browser-verified resident journeys

The in-app browser exercised the real frontend, FastAPI boundary, Single
Orchestrator, Streamable HTTP MCP service and PostgreSQL.

| Resident wording | Result |
| --- | --- |
| “书房的开关坏了” | Electrical repair; room-level location accepted; no model or part number requested; appointment completed. |
| “卫生间洗手池下面一直滴水，我不知道是哪根管子” | Water-leak repair; observable symptom and room-level location accepted. |
| “卧室门锁不好用了……我也不知道是什么型号” | Door-lock repair; model information was not required. |
| “书房墙上的插座不太灵……明天上午家里有人” | Flash extracted the repair facts; deterministic time resolution produced 09:00–12:00 local availability; five valid choices were shown; booking completed. |

The resident is asked only for facts they can reasonably observe and an
availability window. Brand, model, component name, root cause and repair method
remain technician responsibilities.

## Browser-verified failure and human handoff

A controlled provider transport failure proved the exhausted-Flash path:

1. no Scripted or Pro fallback was used;
2. the run reached a terminal `HUMAN_REVIEW` / escalated outcome;
3. the resident received a plain-language handoff message;
4. one pre-ticket human-review case was discoverable in the operator queue;
5. an operator claimed and resolved the case with a handling note;
6. no ticket was fabricated for queue visibility.

The operator confirmation dialog was also exercised after replacing the
incompatible static modal API. SSE cleanup now absorbs expected cancellation
instead of surfacing an unhandled abort.

## Deterministic scheduling safeguards

- Common relative dayparts are resolved centrally: morning 09:00–12:00,
  afternoon 14:00–18:00 and evening 18:00–21:00 in the supplied IANA timezone.
- An explicit clock expression is not overwritten by this convenience rule.
- The resident UI receives at most six ranked choices from the orchestration
  layer; the scheduling service still owns eligibility and conflict checks.
- Choosing a candidate remains non-guaranteed until the transactional booking
  succeeds.

## Operational evidence

- PostgreSQL was healthy during verification.
- Alembic current/head were `20260730_0008`; zero schema drift was reported.
- API and frontend health endpoints returned HTTP 200.
- A real custom-format PostgreSQL backup was created under
  a timestamped directory outside the repository with a SHA-256 sidecar.
- `pg_restore --list` read the backup successfully and reported 211 TOC entries.
- The committed verification and backup scripts provide the repeatable commands.

## Focused regression evidence

- Agent/runtime/hybrid focused suite: 89 tests passed after the final language
  failure and time-resolution changes.
- Frontend suite: 47 tests passed; lint, typecheck and production build passed
  with the bundled modern Node runtime.
- Ruff and Mypy passed for the changed backend modules.
- The production-dependency audit (`npm audit --omit=dev`) reported zero known
  vulnerabilities. The full development-tooling audit still reports 27
  moderate/high transitive advisories in ESLint/Vite/Vitest-era tooling,
  including upstream packages with no current fix; this is tracked without
  forcing an unreviewed toolchain migration into the product closeout.

These are risk-based checks plus live product evidence, not a replacement for a
future production release gate.

## Remaining release boundaries

The following do not block a local product demonstration, but do block an
unqualified claim of production readiness:

- external human approval and a new locked online-model Holdout;
- Shadow and explicitly authorized production Canary evidence;
- approved site-specific contract, responsibility and service-level policies;
- TLS/reverse-proxy deployment, off-device backup retention and restore drill;
- production monitoring, incident ownership and operator staffing;
- resident self-service cancellation and repair acceptance/rejection, which
  intentionally remain human-handled in this release.

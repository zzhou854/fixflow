# Production readiness

## Release decision

FixFlow is `PRODUCTION_READY_WITH_SCRIPTED_PROVIDER` for a reviewed,
single-site deployment. The online interpretation candidate is not qualified,
is disabled by default, and cannot be configured as the primary product
provider.

## Startup gates

Production startup requires:

- a non-placeholder PostgreSQL `DATABASE_URL`;
- a JWT HS256 secret of at least 32 bytes;
- `FIXFLOW_RUNTIME_MODE=production`;
- `FIXFLOW_DEBUG=false`;
- explicit CORS origins and Allowed Hosts without wildcards;
- `FIXFLOW_LLM_PROVIDER=scripted`;
- `FIXFLOW_LLM_EXPERIMENTAL_ENABLED=false`;
- a business database whose Alembic revision exactly matches the shipped head.

The API fails closed before accepting traffic if one of these checks fails.
The JWT secret is never generated at runtime.

## Container evidence

The committed Compose topology builds PostgreSQL/pgvector, one-shot Checkpoint
initialization, one-shot Migration, independent MCP, API, and frontend services.
Long-running services have health checks and restart policies. API and frontend
run as non-root users. API waits for Migration, Checkpoint setup, and MCP health;
frontend waits for API health.

The closure run built both images and started the full topology on Docker Engine
29.6.1 using Linux containers. PostgreSQL, MCP, API, and frontend reported
healthy. `/health` on ports 8000 and 5173 returned HTTP 200. Alembic reported
`20260723_0006 (head)` and no new upgrade operations.

## Product evidence

The full scripted test suite covers authorization, transactions, concurrency,
idempotency, create/clarify/book/select-slot/reschedule/human/safety flows,
Outbox, Trace, UNKNOWN_COMMIT reconciliation, Checkpoint, Replay, and Recovery.
A live container Smoke authenticated a Resident, loaded an authorized property,
created a repair thread, and received the expected typed interrupt.

Shadow tests prove the online observation cannot replace or delay the scripted
result, is not used for response composition, records only bounded safe
metadata, and isolates provider failure. The shadow package imports no MCP,
Repository, Checkpoint, Outbox, or business-mutation facility.

## Limitations

- This is not evidence of public-cloud deployment or internet exposure.
- TLS termination, external secret management, backups, monitoring, capacity
  planning, and organization-specific incident response remain deployment-site
  responsibilities.
- The frontend bundle is functional but large; route/chunk optimization is a
  non-blocking follow-up.
- The online model is not qualified and must remain disabled.

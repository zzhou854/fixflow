# Deployment runbook

## Required configuration

Copy `.env.example` to the ignored `.env` file and replace every password or
secret placeholder. At minimum set `POSTGRES_PASSWORD` and a random
`FIXFLOW_JWT_SECRET` of at least 32 bytes. Keep:

```text
FIXFLOW_LLM_PROVIDER=scripted
FIXFLOW_LLM_EXPERIMENTAL_ENABLED=false
FIXFLOW_LLM_ONLINE_ENABLED=false
FIXFLOW_LLM_SHADOW_ENABLED=false
FIXFLOW_LLM_GROUNDED_RESPONSE_ENABLED=false
FIXFLOW_LLM_ONLINE_QUALIFICATION_STATUS=NOT_ACTIVATED
FIXFLOW_DEBUG=false
```

Phase 1B does not authorize changing these defaults. A future controlled Shadow
requires an explicit reviewed configuration and qualification state; a future
business activation additionally requires a fresh Holdout, Shadow, Canary, and
approval. Never use Scripted as a product fallback after online activation. The
current product candidate is Flash-only: one bounded transport retry or one
schema repair, then a persisted human-review case. DeepSeek Pro is not in the
product route.

For a non-local host, use `docker-compose.production.yml` and set explicit
`FIXFLOW_PRODUCTION_CORS_ORIGINS` and `FIXFLOW_PRODUCTION_ALLOWED_HOSTS`.
Never commit `.env`.

## Build and start

```powershell
cd F:\agent\fixflow
docker compose build
docker compose up -d
docker compose ps
```

The expected dependency order is:

```text
postgres healthy
-> migrate + checkpoint-init complete
-> mcp-server healthy
-> api healthy
-> frontend healthy
```

For production-like local overrides:

```powershell
docker compose -f docker-compose.yml -f docker-compose.production.yml up -d
```

## Verify

```powershell
docker compose ps
Invoke-WebRequest http://127.0.0.1:8000/health
Invoke-WebRequest http://127.0.0.1:5173/health
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic check
```

Expected business head: `20260730_0008`. Do not start the API against an older
or newer unreviewed Schema.

For the local demonstration only:

```powershell
docker compose run --rm api python -m app.dev_seed
```

The idempotent seed creates fictitious local accounts documented in README. It
is not a production identity bootstrap mechanism.

## Operate and stop

```powershell
docker compose logs --tail 200 api mcp-server postgres frontend
docker compose restart api
docker compose down
```

`docker compose down` keeps the named PostgreSQL volume. Do not add `-v` unless
destructive data removal is explicitly intended and approved.

For a repeatable local health and Schema check:

```powershell
.\scripts\operations\verify.ps1
```

## Backup and restore boundary

Create a timestamped custom-format PostgreSQL backup outside the repository:

```powershell
.\scripts\operations\backup.ps1
```

The default destination is `F:\agent\fixflow-backups`; each dump has a SHA-256
sidecar. The script never prints credentials and removes its temporary file from
the container. Backups are operational data and must not be committed.

Restore is intentionally not automated as a casual developer command. Restore
requires a separate target database, verified checksum, compatible Migration
head, maintenance window and explicit operator approval. First prove a dump can
be listed with `pg_restore --list`; never restore over the active development or
production database. Replay artifacts are not database backups.

## Rollback

Deploy the previous reviewed image tag and its compatible Schema. Never edit a
committed Migration. Database downgrade is an explicit maintenance action that
requires backup, compatibility review, and a maintenance window. A Replay
Bundle is not a database rollback mechanism.

## Incident rules

- Keep the default provider scripted.
- If Shadow fails, disable the experimental flag; business processing continues.
- If Migration verification fails, stop and reconcile code/Schema versions.
- If MCP is unhealthy, API startup remains blocked; inspect MCP and PostgreSQL
  logs rather than bypassing dependency health.
- Do not expose raw Checkpoints, traces, provider payloads, JWTs, or database
  credentials during diagnosis.

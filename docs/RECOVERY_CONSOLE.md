# Recovery Console

The Recovery Console is an Operator-only, read-only diagnostic surface embedded
in the existing workbench. It lists original Runs for a ticket-linked thread,
shows Bundle availability and integrity metadata, starts deterministic
verification, displays safe mismatch summaries, and keeps current PostgreSQL
facts visually separate from original replay evidence.

## Authorization

All routes require an active Operator JWT. A Run is readable only through the
existing ticket-linked Operator Trace authorization boundary. Knowledge of a
Run ID, Bundle ID, or Execution ID is insufficient. Operator-action runs are
linked through their typed target ticket and revalidated through the formal
Application query service. Residents receive 403. Unknown or unrelated
identifiers return 404/403 without exposing existence.

The API never returns full conversations, raw MCP/provider responses,
Checkpoint data, raw idempotency keys, internal request payloads, SQL,
credentials, or exception stacks.

## API

```text
GET  /api/v1/operator/threads/{thread_id}/replay-runs
GET  /api/v1/operator/runs/{run_id}/replay
GET  /api/v1/operator/replay-bundles/{bundle_id}
POST /api/v1/operator/runs/{run_id}/replay/verify
GET  /api/v1/operator/replay-executions/{execution_id}
```

Verify requires `Idempotency-Key`. The scope is Operator actor plus key:
same key and same request returns the same Execution; reuse for different
content returns 409. Verification is synchronous and bounded by the configured
timeout, but its terminal result remains queryable if the HTTP connection is
lost.

## UI semantics

The Run list shows trigger, original status, replayability, Bundle status, and
latest verification status. Details show schema versions, runtime revision,
checksum presence, step count, final status, safe mismatch type/key/summary,
and the deterministic recovery recommendation.

There are no controls to “apply Replay”, “retry mutation”, “restore database”,
or “modify Checkpoint”. The sole action is **验证确定性重放**. Historical Runs
without a Bundle are shown as unavailable rather than backfilled. Residents do
not see internal Replay evidence in the first release.

Current business state is refreshed independently from PostgreSQL and labelled
as current fact. It is not used as Replay input and cannot alter the original
comparison.

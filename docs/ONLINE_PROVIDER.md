# Controlled online-provider boundary

## Status

Commercial-hardening phase 1B supplies a candidate integration, not an
activation:

```text
Default provider: scripted
Online provider: NOT_ACTIVATED
DeepSeek production traffic: false
```

The consumed qualification corpus is not reusable as a new Holdout. This phase
runs no new Holdout, formal Shadow campaign, Canary, or production activation.

## Capability split

`Structured Understanding Provider` may extract only schema-validated,
evidence-supported resident facts. Deterministic code continues to own final
Intent, Missing Fields, Safety, authorization, workflow transitions, scheduling,
MCP calls, database mutation, and human-review creation.

`Grounded Response Provider` may choose a constrained tone and a subset of
allowlisted fact identifiers. Server-owned templates render the response. It
cannot change `message_outcome`, `required_user_action`, ticket/appointment
facts, or policy facts. Safety, permission, mutation-result, human-review,
UNKNOWN_COMMIT, cancellation, and closure messages bypass the model. Optional
drafting failure falls back to the deterministic template.

Neither interface imports MCP, business repositories, Unit of Work, or
Application mutation services.

## Flash to Pro routing

One request owns one monotonic model-call deadline, initially 25 seconds. It
covers Flash, its single transport retry, one Flash schema-repair call, Pro, and
optional grounded drafting.

```text
Flash
  -> one retry for TIMEOUT / RATE_LIMITED / CONNECTION_FAILED / UPSTREAM_5XX
  -> one schema repair for SCHEMA_VALIDATION_FAILED
  -> Pro (one call)
  -> PROVIDER_EXHAUSTED
  -> durable human review by the existing reliability service
```

Authentication, permission, invalid configuration, content-policy rejection,
business conflicts, policy conflict/insufficiency, safety escalation, and user
requests for a human are not availability retries. Schema repair receives the
original bounded messages, strict schema, and a sanitized validation summary;
it receives no suggested business answer.

Stable provider errors are `TIMEOUT`, `RATE_LIMITED`, `CONNECTION_FAILED`,
`UPSTREAM_5XX`, `AUTHENTICATION_FAILED`, `INVALID_CONFIGURATION`,
`MALFORMED_RESPONSE`, `SCHEMA_VALIDATION_FAILED`, `CONTENT_POLICY_BLOCKED`,
`BUDGET_EXHAUSTED`, `CIRCUIT_OPEN`, and `UNKNOWN_PROVIDER_ERROR`.

Flash and Pro have independent concurrency-safe process-local circuit breakers
with `CLOSED`, `OPEN`, and single-probe `HALF_OPEN` states. Only transport
failures count. Process-local state does not coordinate multiple API instances;
the circuit port permits a future shared implementation if scale requires it.

## Qualification and activation gate

Runtime modes are `development`, `demo_safe`, and `production_candidate`.
Qualification states are:

```text
NOT_ACTIVATED
CONTRACT_PASSED
DEV_REGRESSION_PASSED
HOLDOUT_PASSED
SHADOW_PASSED
CANARY_PASSED
APPROVED
```

This phase may establish only contract/development evidence and never changes a
status automatically. Development permits explicit test calls. Demo-safe permits
Shadow only when both switches and contract evidence exist; its result never
controls business behavior. Business calls require `production_candidate` and
`APPROVED`, which this phase does not grant.

## Shadow-ready evidence

Revision `20260730_0008` adds `llm_shadow_runs`. The Scripted result returns
immediately; the candidate sees the same sanitized `InterpretMessageInput`.
Its output cannot enter Graph State or trigger MCP or business mutations. The
best-effort shadow transaction is decoupled from the business transaction.

Stored evidence is limited to source Run ID, provider/model, prompt/schema
versions, status, latency, safe error code, structured-result SHA-256, and
timestamp. No user text, prompt text, raw response, token, credential, transport
payload, or business idempotency material is stored.

## Development evidence

Contract and deterministic Development Regression tests use fake providers,
MockTransport, and fake clocks; they do not call a real DeepSeek endpoint.
Metrics keep Flash first-pass, schema repair, Pro fallback, routed success,
human escalation, latency, and errors separate. They prove integration behavior,
not model quality or formal qualification.

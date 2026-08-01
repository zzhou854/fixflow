# Controlled online-provider boundary

## Status

Commercial-hardening phases 1B through 1D-B supply a candidate integration and
development evidence. The subsequent development-account canary remains an
allowlisted development facility, not a product activation:

```text
Default provider: scripted
Online provider: NOT_ACTIVATED
DeepSeek production traffic: false
```

When all four default-off canary switches are enabled, only an authenticated
`user_id` in `FIXFLOW_ONLINE_CANARY_USER_IDS` may use online Structured
Understanding and/or Grounded Response. The identifier comes from the verified
JWT/account context and cannot be supplied by a browser request. All other
accounts remain on Scripted. Canary configuration is rejected outside the
`development` runtime, when the product default is not `scripted`, or when
Shadow is enabled. Docker production configuration keeps all canary switches
off.

The switches are `FIXFLOW_ONLINE_CANARY_ENABLED`,
`FIXFLOW_ONLINE_CANARY_USER_IDS`,
`FIXFLOW_ONLINE_STRUCTURED_UNDERSTANDING_ENABLED`, and
`FIXFLOW_ONLINE_GROUNDED_RESPONSE_ENABLED`. They authorize language calls only;
deterministic routing, authorization, policy, workflow, MCP, and persistence
boundaries are unchanged.

Historical qualification corpora are not reusable as new Holdouts. Phase 1D-A
assets received a `CHANGES_REQUIRED` review disposition without any live call.
Phase 1D-B adds revised, externally stored 2.1.0/1.1.0 Suites whose manifests
are `SEALED`, whose fresh approval is `PENDING_APPROVAL`, and whose live-call
count is zero. It runs no Holdout inference, formal Shadow campaign, Canary,
or production activation.

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

Every grounded call receives only allowlisted business facts plus the
server-owned `message_outcome`, `business_status`, `template_id`, and typed
`required_user_action`. The provider may choose phrasing and fact references;
it cannot change those fields. Trace evidence records whether a real model was
used and whether `deterministic_template_fallback=true`, while resident
responses never disclose provider or model names.

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

Phase 1C adds an explicitly gated real-provider Smoke and three complete
120-case synthetic Development Regression runs (Flash, Pro, and Flash-to-Pro).
All passed the pre-existing frozen development gate. A 24-case two-repeat
sample passed all control-plane stability thresholds. See
`docs/DEVELOPMENT_REGRESSION.md`.

This supports a future human decision about `DEV_REGRESSION_PASSED`; it does
not set that status automatically and does not establish Holdout, Shadow,
Canary, or production qualification. New Holdout sealing tools and the
no-reuse protocol are documented in `docs/HOLDOUT_PROTOCOL.md`.

Phase 1D-A introduced independent approval, Golden review,
Prompt/Schema/scorer/gate/code binding, one-time concurrent execution locking,
append-only aggregate results, and separate Structured/Grounded scorers.
Phase 1D-B preserves its failed review evidence, adds adverse-disposition
enforcement, revises field-level evidence and Grounded hard gates, and prepares
a fresh all-240-case review packet. It does not execute or pass the Holdout.

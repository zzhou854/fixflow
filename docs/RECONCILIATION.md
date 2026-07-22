# UNKNOWN_COMMIT reconciliation

FixFlow classifies mutation results as `KNOWN_SUCCESS`, `KNOWN_FAILURE`,
`NOT_SENT`, or `UNKNOWN_COMMIT`. Only uncertainty after a formal mutation may
have been dispatched creates a durable case. Three Resident Agent actions use
MCP: ticket create, appointment book, and appointment reschedule. Operator
escalation uses the existing Operator Action/Application boundary; it does not
invent an MCP hop. All four share the same delivery classification, durable
Case, evidence query, fenced worker, and Trace semantics.

The authoritative outcome is `COMMITTED` only when the durable business
idempotency row (including operation and request identities), canonical result,
business aggregate and same-transaction Outbox evidence agree. It is
`NOT_COMMITTED` only when all authoritative primary-database evidence is absent.
Partial or mismatched evidence is `INCONSISTENT` and becomes `MANUAL_REVIEW`.

Cases move `PENDING -> PROCESSING` under `FOR UPDATE SKIP LOCKED` and a fresh
UUID claim token, then to `RESOLVED_COMMITTED`, `RESOLVED_NOT_COMMITTED`, or
`MANUAL_REVIEW`; transient query failures return to `PENDING` with backoff.
Every update is fenced by worker and token. The worker never reads Checkpoint,
runs the graph, or resends a mutation. A not-committed result only permits the
authenticated owner to continue later with the original operation identity.
For escalation, that owner is the original active Operator, never a Resident or
synthetic service principal.

Case and API projections never expose the raw idempotency key. Reconciliation
is not Replay, event sourcing, a Saga framework, or a general distributed
transaction guarantee; deterministic Replay remains Task 12.

## Delivery and recovery contract

Only a complete, schema-valid response whose action, operation ID, and resource
match is `KNOWN_SUCCESS`. Explicit permission, not-found, validation, version,
time, idempotency, and unsupported-operation results are `KNOWN_FAILURE` and do
not create a Case. `NOT_SENT` is restricted to provable pre-send failures and
retains the same operation/key. Any post-send timeout, reset, truncated or
malformed response, post-commit response loss, or validated-result/checkpoint
gap is `UNKNOWN_COMMIT`: the Agent stops as `FAILED_SAFE`, enters
`RECONCILIATION_PENDING`, and returns `RECONCILIATION_PENDING` (HTTP 202 at the
resident mutation API).

`get_operation_outcome` is the ninth discovered MCP tool and is read-only. Its
strict request is built only from the frozen Case identity. Before returning
evidence it revalidates active actor/user/property authority. COMMITTED requires
matching idempotency scope, operation, request fingerprint, canonical result,
entity/version/property ownership, and the action-specific Outbox event.
NOT_COMMITTED requires absence of both idempotency and Outbox evidence after a
successful authoritative read. Every partial or contradictory combination is
INCONSISTENT and immediately routes to manual review.

On Resident owner read/message/resume, identity and current property authority are checked
before the Case. Pending/processing cases cannot retry. A committed result is
verified again through the formal ticket snapshot query before pending state is
cleared. A not-committed result preserves the original pending operation and
business idempotency key for the owner's next Resume. Manual review preserves the
Case and pending operation and cannot be bypassed by another resident message.

An uncertain Operator escalation returns HTTP 202 `RECONCILIATION_PENDING`.
Same-key replay while the Case is pending, committed, or manual returns the
cached 202 without another Action Run or dispatch. Only after the evidence
worker records `RESOLVED_NOT_COMMITTED` may the original Operator repeat the
exact payload and key; the API then releases that completed replay entry and
formally redispatches once. `RESOLVED_COMMITTED` refreshes the ticket without a
second escalation, while `MANUAL_REVIEW` never redispatches.

Operator list/detail/recheck APIs return only a sanitized projection. Recheck is
allowed only for PENDING and merely advances the next read; operators cannot set
the resolution or resend a mutation. The resident UI blocks mutation-producing
controls while pending, processing, or manual review. The operator UI provides
filters, paging, safe detail, refresh, and read-only recheck without raw payloads.
Its escalation control is disabled during reconciliation and re-enabled with
the original API key only after `RESOLVED_NOT_COMMITTED`.

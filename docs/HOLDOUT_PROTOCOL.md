# Independent Holdout qualification protocol

## Current review pointer

The latest repository-recorded package is Structured 2.2.0 / Grounded 1.2.0;
see HOLDOUT_REVISION_V2.md. It remains subject to independent human approval.
Before any execution, verify the private manifests and signatures; repository
documentation alone is not authorization. No qualification is run by this update.

## Historical phase 1D-B state

Commercial-hardening phase 1D-B prepares revised synthetic qualification
suites after phase 1D-A independent-assisted review returned
`REVIEW_FAILED_CHANGES_REQUIRED`. It performs no Provider call and grants no
approval:

```text
Structured 2.1.0 manifest: SEALED
Grounded 1.1.0 manifest: SEALED
Golden review: REVIEW_PENDING
Approval: PENDING_APPROVAL
live_call_count: 0
Default Provider: scripted
Online Provider: NOT_ACTIVATED
```

The original 2.0.0/1.0.0 manifests remain immutable `SEALED` evidence with an
append-only `CHANGES_REQUIRED` disposition, zero live calls and no qualification
execution. The executor rejects `CHANGES_REQUIRED`, `SUPERSEDED` and `REJECTED`
versions before network dispatch.

`SEALED` means that revised data, Golden, scorer, gate, Prompt, schema,
normalization, router and runtime code identities are immutable. It does not
mean that the candidate passed the Holdout.

## Private asset boundary

The private data and Golden files are stored outside the Git worktree:

```text
F:\agent\fixflow-holdouts\
  resident_interpretation_holdout_2.0.0\
    dataset.jsonl
    golden.jsonl
    manifest.json
  grounded_response_holdout_1.0.0\
    dataset.jsonl
    golden.jsonl
    manifest.json
  resident_interpretation_holdout_2.1.0\
    dataset.jsonl
    golden.jsonl
    automated_checks.jsonl
    manifest.json
  grounded_response_holdout_1.1.0\
    dataset.jsonl
    golden.jsonl
    automated_checks.jsonl
    manifest.json
  dispositions\
    holdout_dispositions.jsonl
  approval\
    holdout_approval.pending.json
    holdout_approval.revised.pending.json
```

The root has protected Windows ACL inheritance. The observed allowed
principals are the current local user, `SYSTEM`, and local Administrators.
No other principal was present after verification. The directory is a sibling
of the repository, so it is outside every repository Docker build context.

Git contains only content hashes, distributions, scorer/gate definitions,
sealed manifests, duplicate-scan statistics and pending approval material.
It contains no Holdout utterance, complete conversation, Golden label, raw
Provider output or model-targeting detail.

## Suite identities

### Structured Understanding

```text
dataset: resident_interpretation_holdout@2.1.0
cases: 180
single-turn: 120
multi-turn: 60
WATER_LEAK / ELECTRICAL / DOOR_LOCK: 60 each
```

It covers evidence-supported fact extraction, final Intent, Clarification,
Missing Fields, Safety, critical Safety, explicit human requests,
authorization boundaries, abstention, negation, correction and multi-turn
recovery.

### Grounded Response

```text
dataset: grounded_response_holdout@1.1.0
cases: 60
categories: 15
cases per category: 4
```

The categories include ticket and appointment success/failure, information and
slot interrupts, authorization denial, Policy review, Safety escalation,
human-task success/failure, mutation reconciliation, unsupported requests, and
separate ticket cancellation, ticket closure and appointment cancellation. The
model may select presentation only. Server-owned outcome, required action and
facts remain authoritative.

## Isolation scan

The offline scan compares each suite with:

- `resident_interpretation@1.0.0`;
- all seven historical Challenge versions;
- the historical architecture-3 Holdout;
- every backend unit/integration test source file;
- documentation examples;
- Prompt and few-shot assets;
- the other new suite;
- every other case in the same candidate suite.

It checks Case ID, exact text, NFKC/case/whitespace normalization,
punctuationless text, joined multi-turn text, known fingerprints and
near-duplicates. Near-duplicates use
`unicode-nfkc-punctuationless-sequence-matcher@1.0.0`, threshold `0.88`.
A mathematically safe length upper bound avoids comparisons that cannot reach
the threshold; it does not change the decision rule.

All duplicate categories and near-duplicate counts are zero. The report stores
only counts, Case IDs, sources and similarities; with zero findings it exposes
no private text.

## Golden review

Automated revised-package checks validate:

- Pydantic schema;
- Dataset/Golden Case-ID equality and uniqueness;
- conversation equality across the two private files;
- exact field/value/source/Turn/span/offset evidence bindings;
- negated and stale evidence invalidation;
- explicit `known_issue_fields` retention and correction;
- category and single/multi-turn totals;
- Missing Fields uniqueness;
- fact/null exclusivity;
- critical Safety consistency;
- `REQUEST_HUMAN` priority;
- Grounded outcome, action, required/forbidden information, allowlist,
  identifier/schedule permissions, technical leakage, cross-entity status and
  deterministic Safety-template rules.

This is not an independent human review. A second reviewer has not signed the
Golden, so both manifests retain
`AUTOMATED_CHECKED_REVIEW_PENDING`. Independent approval must explicitly
confirm the Golden review before the manifests can become `APPROVED`.

## Frozen identities

Both manifests bind:

```text
runtime code commit
Prompt version and SHA-256
Schema version and SHA-256
normalization version and SHA-256
scorer version and SHA-256
gate version and SHA-256
Provider router version
Dataset SHA-256
Golden SHA-256
```

Any mismatch changes the manifest to `INVALIDATED`; an approval cannot be
carried forward. The runtime identity is the committed revised qualification
implementation `990bbfcbf0c03e40c4b339be71b959a0065a30ba`. Later
documentation-only commits do not alter that frozen runtime; the executor must
run the frozen commit in a clean worktree.

## Lifecycle and independent approval

```text
DRAFT -> SEALED -> APPROVED -> CONSUMED
   \         \          \
    +---------+-----------> INVALIDATED
```

- `DRAFT`: private authoring is allowed; execution is forbidden.
- `SEALED`: all content and evaluation identities are frozen; execution remains
  forbidden.
- `APPROVED`: a separate human approval and independent Golden review match
  both sealed manifests; execution may claim the one-time right.
- `CONSUMED`: the first request was about to be dispatched. The suite can never
  again be a blind qualification Holdout.
- `INVALIDATED`: content or any frozen identity changed.

The pending JSON packet deliberately has no `approver` and no `decision`.
Only a separate `HoldoutApprovalRecord` accepts `APPROVED` or `REJECTED`.
Codex does not manufacture an approver or perform `SEALED -> APPROVED`.

## First-call lock

Before a network request, the executor must verify:

1. both manifests project back to the hashes signed by the approval;
2. the independent decision is `APPROVED`;
3. independent Golden review was confirmed;
4. Dataset and Golden hashes and counts still match;
5. code, Prompt, schema, normalization, scorer, gate and router identities match;
6. credentials exist;
7. the default business Provider is still `scripted`;
8. business traffic is not routed to the online candidate;
9. the target manifest is `APPROVED` with zero live calls.

An exclusive filesystem claim serializes competing executors. The winner
atomically replaces the manifest with:

```text
status = CONSUMED
first_live_call_at = <UTC timestamp>
live_call_count = 1
```

This durable change occurs before dispatch. A failed first request therefore
still consumes the suite. Concurrent or later attempts fail. Aggregate result
files use a unique `qualification_run_id` and exclusive creation, so an earlier
result cannot be overwritten. Interrupted executions append `ABORTED` evidence
and leave the Holdout consumed.

## Frozen scorer and gates

Structured scorer `1.1.0` adds field-level evidence precision/recall,
unsupported-field, mismatched-evidence, stale-evidence and negation-error
metrics while retaining correct abstention and authorization accuracy.
Grounded scorer `1.1.0` promotes required-information coverage and
forbidden-information violations into aggregate metrics, then checks
outcome/action preservation, allowlisted facts, unsupported claims, fabricated
identifiers/schedules, unauthorized promises, template mapping, entity/status
semantics, technical leakage and Safety templates.

The historical `resident_interpretation_gate@1.0.0` and hash
`7391042124ab2aec9eccd796b4493a4184e0722748f55528c5cb22e7aa1b134e`
remain unchanged. The structured gate inherits those thresholds and adds:

```text
Authorization Boundary Accuracy = 100%
Field-level Evidence Precision >= 99%
Field-level Evidence Recall >= 99%
Unsupported Field Rate = 0%
Mismatched / Stale / Negation Evidence Rate = 0%
Correct Abstention Rate >= 99%
Provider Exhausted Rate = 0%
```

The Grounded gate requires 100% required-information coverage,
outcome/action/allowlist/template/status/Safety compliance and zero forbidden
information, unsupported claims, fabricated IDs, fabricated schedules,
unauthorized promises and technical leakage. These additions were fixed before
any live call. They are pending independent approval and cannot be changed
based on future Holdout results.

## One-time execution and result boundary

The future approved run may store only safe aggregate metrics, model/router
identity, latency and error counts, hashes, timestamps and the final gate
decision. Results are append-only. It must not store credentials, headers,
full endpoint, private text, Golden labels, raw HTTP, complete Prompt, raw
Provider response, private reasoning or business records.

The current stage does not run the executor. `Holdout prepared` must never be
reported as `Holdout passed`.

# Phase 1D-A Holdout approval packet

## Decision requested

This packet asks an independent reviewer whether the sealed qualification
design may move from `SEALED` to `APPROVED`. It does not approve execution and
does not include private cases or Golden labels.

```text
Current decision: PENDING_APPROVAL
Holdout online calls: 0
DeepSeek business traffic: false
Default Provider: scripted
```

## Suite summary

| Suite | Dataset | Cases | Distribution | Status |
| --- | --- | ---: | --- | --- |
| Structured Understanding | `resident_interpretation_holdout@2.0.0` | 180 | 120 single, 60 multi; three issue categories × 60 | SEALED |
| Grounded Response | `grounded_response_holdout@1.0.0` | 60 | 15 business-result categories × 4 | SEALED |

The source is synthetic engineering-authored data. It contains no real
resident name, address, telephone number, ticket, conversation or employee
identity. Fictional identifiers are limited to explicitly synthetic labels.

## Content identities

| Suite | Dataset SHA-256 | Golden SHA-256 |
| --- | --- | --- |
| Structured | `ea7f4efdc5994e1c11444999c4171306caea98c431b41df08991acbc592a8c07` | `cf93a02c8013ab66d061ff56de5003a5bba8d0f93c69b244284f5a822b789822` |
| Grounded | `a11949a6db8168398d6c7eb06882eed59da32b3e09a9b4d5ec8121527008c146` | `9fb6104833c473ef727a866ddbaf6cee3a75e9b2dfcd2cb58eda8a86c9037a83` |

Private files reside under `F:\agent\fixflow-holdouts`, outside Git and Docker
contexts. ACL verification found only the current local user, `SYSTEM`, and
local Administrators.

## Isolation and privacy evidence

Reference scope includes the development corpus, seven historical Challenge
versions, old Holdout, all backend tests, docs, Prompt examples and both new
suites. The scan found:

```text
Case ID duplicates: 0
Exact text duplicates: 0
Normalized text duplicates: 0
Punctuationless duplicates: 0
Joined multi-turn duplicates: 0
Known fingerprint duplicates: 0
Candidate internal duplicates: 0
Near-duplicates at threshold 0.88: 0
```

The Git package was scanned for Holdout utterances, Golden structures, raw
responses, credentials and real personal data. Only manifests, hashes,
statistics and versioned scorer/gate definitions are present.

## Golden review evidence and open action

Automated schema, cross-file, evidence-span and deterministic rule checks pass
for all 240 cases. That is review round one only.

Required independent action:

1. Review Golden consistency for Missing Fields, Safety, human priority,
   authorization, negation/correction and Grounded forbidden claims.
2. Record reviewer identity and UTC timestamp.
3. Confirm `golden_review_confirmed=true`.
4. Choose exactly `APPROVED` or `REJECTED`.
5. Sign the exact two sealed Manifest hashes in
   `backend/evals/holdout/approval/holdout_approval.pending.json`.

Until this is complete, `APPROVED` execution is technically blocked.

## Scorer and gate identities

| Suite | Scorer | SHA-256 | Gate | SHA-256 |
| --- | --- | --- | --- | --- |
| Structured | `resident_interpretation_holdout_scorer@1.0.0` | `5a22dab5c0678f30367a179e191fe3015ceb8ff24c39997e85eef2035822dc27` | `resident_interpretation_holdout_gate@1.0.0` | `f6dc16dfef629fa55b6f6a12763bdb6c26e028870b638f65229e2a78be3069aa` |
| Grounded | `grounded_response_holdout_scorer@1.0.0` | `2cf4ec6f84a9e21ba84f72f160d50c6a1a5b3882c338522031d93a4f4ac82c0b` | `grounded_response_holdout_gate@1.0.0` | `0ee719e293ed6b71a1d9d2b682ce696f5a60984f5af3fd84c75cbccc806f30d0` |

Metric names, directions and exact thresholds are in the versioned JSON files.
The Structured gate inherits the historical Policy `1.0.0` without weakening
it, then adds evidence, abstention and authorization rules. Critical Safety,
explicit human requests, authorization, fabricated IDs/schedules, promises,
Safety templates and technical leakage are hard boundaries.

## Frozen runtime identities

```text
Runtime commit:
63b78e08ad97cab40314e1f3b34e1f6b8671e5ba

Structured Prompt:
resident_fact_extraction@2.0.0
5b2cf61635f541c5db8e72f092f42be763210d006b6f69ea5de275d6b25f6d6b

Structured Schema:
resident-facts-v2
00fd9026ef5d8e6d55b9a0ac4b51973ba4084693665ce875d21d24d28fe6727c

Grounded Prompt:
grounded_response@1.0.0
6cb80356371a1e086ec8e355cee72a26de48eb5ffe75a9502e06a7182a87ab1d

Grounded Schema:
grounded-response-draft-v1
6cebaa74f1120b66c4bc41e547aeed27b6b7291e79001e442f9c8bccde2609ff

Router:
deepseek-flash-pro-router-v1
```

Normalization hashes and all identities are embedded in the manifests. Any
change invalidates this approval request.

## Cost and duration estimate

There are 240 logical cases. Current deterministic Safety/mutation templates
bypass model drafting for 36 Grounded cases, leaving up to 204 candidate model
operations per complete pass (180 Structured + 24 Grounded). If a second fixed
stability repeat is included in the independent execution approval, the total
is up to 408 candidate operations.

Each operation has one shared 25-second budget. Conservative upper bounds,
excluding deliberate pacing and circuit cooling, are:

```text
one pass: 85 minutes
two repeats: 170 minutes
```

Flash transport retry/schema repair and Pro fallback share that budget. The
worst authorized upstream-attempt bound is three per candidate operation.
Actual token/currency cost must be calculated from the Provider price in force
at approval time; this packet does not guess a mutable price.

## Risks

- Both suites are synthetic and do not prove real resident generalization.
- The Grounded scorer uses deterministic claim classes and cannot prove every
  possible linguistic hallucination.
- Windows local Administrator and `SYSTEM` access cannot be removed from the
  host security boundary.
- Filesystem locking is correct for this single Windows host; a future
  distributed executor would need a shared atomic store.
- A first-call infrastructure failure still permanently consumes the Holdout.
- Approval of the Holdout does not approve Shadow, Canary or production use.

## One-time execution rule

The executor validates the approval and all identities, then persists
`CONSUMED` before the first request. It cannot re-run a consumed Holdout.
Failures produce an append-only `ABORTED` or completed record; the first result
is never overwritten or replaced by a better run.

If the candidate fails, the result remains evidence. Prompt, schema, scorer,
gate and difficult cases cannot be changed and rerun on these suites as a new
blind qualification.

## Approval signature

Complete a separate approval record only after the independent review:

```text
approval_id:
approver:
approval_timestamp (UTC):
decision: APPROVED | REJECTED
golden_review_confirmed:
notes:
```

The machine validator also requires the two Manifest SHA-256 values and the
combined code/Prompt/schema/scorer/gate identity from the pending JSON packet.
Codex must not fill these human identity and decision fields.

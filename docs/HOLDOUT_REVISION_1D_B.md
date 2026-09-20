# Commercial-hardening phase 1D-B revision report

## Scope and result

Phase 1D-B repairs evaluation-governance defects found by the separate
phase-1D-A Golden review. It does not call DeepSeek, execute a qualification
Holdout, run Shadow or Canary, activate an online Provider, change the three
repair categories, change the production interpretation Schema, or add a
database migration.

```text
Previous review: REVIEW_FAILED_CHANGES_REQUIRED
Previous live calls: 0
Revised Structured: resident_interpretation_holdout@2.1.0
Revised Grounded: grounded_response_holdout@1.1.0
Revised manifests: SEALED
Revised approval: PENDING_APPROVAL
Revised live calls: 0
Independent revised review: NOT_STARTED
Default Provider: scripted
```

This is preparation evidence only. It is not a passing Holdout or model
qualification.

## Source and review integrity

The original private assets remain byte-identical:

| Asset | SHA-256 |
| --- | --- |
| Structured Dataset 2.0.0 | `ea7f4efdc5994e1c11444999c4171306caea98c431b41df08991acbc592a8c07` |
| Structured Golden 2.0.0 | `cf93a02c8013ab66d061ff56de5003a5bba8d0f93c69b244284f5a822b789822` |
| Grounded Dataset 1.0.0 | `a11949a6db8168398d6c7eb06882eed59da32b3e09a9b4d5ec8121527008c146` |
| Grounded Golden 1.0.0 | `9fb6104833c473ef727a866ddbaf6cee3a75e9b2dfcd2cb58eda8a86c9037a83` |

The five review artifacts were hash-verified before issue mapping. All 85
unique issue IDs were mapped and resolved in a private, machine-readable
record:

```text
BLOCKER: 15 / resolved 15
MAJOR: 55 / resolved 55
MINOR: 15 / resolved 15
Unmapped: 0
```

An append-only disposition marks each old suite `CHANGES_REQUIRED`,
`qualification_executed=false`, `live_call_count=0`, and points to the revised
version. It does not alter the old sealed manifests or manufacture a reviewer
signature.

## Structured scorer 1.1.0

Each extracted fact binds:

```text
field_name
normalized_value
evidence_source
evidence_turn_id
evidence_span
evidence_start
evidence_end
```

The revised scorer validates the exact user Turn or explicitly trusted
`known_issue_fields`; it rejects wrong-field, negated, stale, partial,
mispositioned, cross-Turn, inferred and fabricated evidence. New aggregate
metrics include field-level evidence precision/recall, unsupported-field rate,
mismatched-evidence rate, stale-evidence rate and negation-evidence error rate.

The production Prompt and structured response Schema did not change. The
review established an evaluation binding defect, not a production-contract
defect.

## Grounded scorer and gate 1.1.0

Required-information coverage and forbidden-information violations now enter
the formal aggregate and gate. Hard boundaries remain:

```text
Fabricated Identifier Rate = 0%
Fabricated Schedule Rate = 0%
Unauthorized Promise Rate = 0%
Technical Leakage Rate = 0%
Safety Template Compliance = 100%
Outcome Preservation Rate = 100%
Required Action Preservation Rate = 100%
```

The technical-leakage detector covers English/Chinese, case, underscore,
hyphen and spacing variants for Provider/model identities, Schema/Prompt,
UUID/SSE/Trace/Replay/Checkpoint/LangGraph/LangChain/MCP, idempotency keys,
internal IDs and workflow states. Business-language false positives have
explicit negative tests.

Identifiers and schedules are permitted only when the input contains a
concrete verified displayable value. Candidate windows, date-only facts and
internal UUIDs do not grant display permission.

## Response semantics

Deterministic templates now distinguish:

```text
TICKET_CANCELLED
TICKET_CLOSED
APPOINTMENT_CANCELLED
```

Concrete failure and pending mappings replace vague `GENERIC_UPDATE` cases:

```text
TICKET_CREATION_FAILED
APPOINTMENT_PENDING
HUMAN_REVIEW_CREATED
HUMAN_REVIEW_CREATION_FAILED
POLICY_REVIEW_REQUIRED
MUTATION_RECONCILIATION_PENDING
UNSUPPORTED_REQUEST
AUTHORIZATION_DENIED
```

The scorer checks template identity, outcome, required action, required
information and forbidden cross-entity/status claims.

## Multi-turn context

All revised multi-turn cases separate:

```text
conversation_messages
known_issue_fields
current_user_turn
```

They cover retention of unaffected facts, explicit replacement, revocation
without a new value, “刚才说错了”, pronoun-style confirmation, two-field
continuation, cancellation of automatic handling, and unauthorized third-party
property requests. Historical conversation text alone is never treated as
persisted truth.

## New asset identities

| Asset | SHA-256 |
| --- | --- |
| Structured Dataset 2.1.0 | `a93bf7d3d049d878f20411065bbc6e6bff2f96a06d1428b76c2ecc0beaea1c51` |
| Structured Golden 2.1.0 | `0f05b22dd5f56549abf93f240b3430da6d4ae778258a91cf2aee395f8bff72c9` |
| Grounded Dataset 1.1.0 | `4a77e3685cfa19eb46d0cee12aa28d14559e40f0b7965dfa76b95dc0e0ab80dd` |
| Grounded Golden 1.1.0 | `98be07cc2a8421ea91c84938d19a30205d049d1c4326b49d1089369a10fdcd9c` |
| Structured Scorer 1.1.0 | `c6ff380349f6dfa4437a4617a74a3e9f4682164a2f77565ce9f51e170229d94b` |
| Structured Gate 1.1.0 | `13964a6d2cae2c264a3e95e404d7c2d4e85ebe8d23c06b0affd34877c9e7ba07` |
| Grounded Scorer 1.1.0 | `9b1deb6015fb2dac66c3de29e67c6a1802526bdc3a30550c7ea9a8d0abd4dba9` |
| Grounded Gate 1.1.0 | `e60bea4bec0ce63347a5b9f501a79909903bfd9de18d43c10214b7323c175464` |

The revised runtime code identity is
`990bbfcbf0c03e40c4b339be71b959a0065a30ba`.

## Isolation and review boundary

Exact, normalized, punctuationless, joined-turn, fingerprint, internal and
near-duplicate scans all report zero findings at the unchanged `0.88`
threshold. The comparison set includes Development, seven historical Challenge
versions, historical and phase-1D-A Holdouts, tests, docs, Prompt sources and
both revised suites.

Automated checks cover all 240 revised cases, but the status remains
`AUTOMATED_CHECKED_REVIEW_PENDING`. A fresh review packet initializes all 240
cases as `UNREVIEWED`; none of the previous 159 assisted accepts is inherited.

## Post-seal internal adversarial review

The repository-safe report
`backend/evals/holdout/reports/internal_assisted_review.revised.json` binds the
second offline internal review without exposing case or Golden text. It records
240 assisted accepts and zero unresolved issues or uncertain decisions. The
private evidence includes one preserved failed reviewer run and the corrected
run; neither is an independent human review or approval.

The next lifecycle state is an unsigned private request with
`decision=PENDING_HUMAN_SIGNATURE` and `signature=null`. Formal inference,
Shadow, Canary, and online activation remain forbidden.

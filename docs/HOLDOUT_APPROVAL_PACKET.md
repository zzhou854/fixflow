# Phase 1D-B revised Holdout approval packet

> Historical 2.1.0 / 1.1.0 package, not the latest approval request. See
> HOLDOUT_REVISION_V2.md for Structured 2.2.0 / Grounded 1.2.0. This file grants
> no approval and must not be used to launch qualification of an obsolete version.

## Decision requested

This packet requests a new independent review of every revised Golden case. It
does not approve execution and contains no private utterance or Golden label.

```text
Structured: resident_interpretation_holdout@2.1.0
Grounded: grounded_response_holdout@1.1.0
Manifest status: SEALED
Golden review: AUTOMATED_CHECKED_REVIEW_PENDING
Approval: PENDING_APPROVAL
live_call_count: 0
Default Provider: scripted
Online Provider: NOT_ACTIVATED
```

Phase 1D-A review returned `REVIEW_FAILED_CHANGES_REQUIRED`. Its original
2.0.0/1.0.0 data, Golden, manifests, approval request and review evidence remain
unchanged. An append-only external disposition marks both old versions
`CHANGES_REQUIRED`, `qualification_executed=false`, and `live_call_count=0`.
The executor rejects an adversely disposed package.

## Revised suite summary

| Suite | Cases | Coverage | Status |
| --- | ---: | --- | --- |
| Structured Understanding 2.1.0 | 180 | 120 single-turn, 60 multi-turn; three issue categories × 60 | SEALED |
| Grounded Response 1.1.0 | 60 | 15 result categories × 4, including distinct cancellation/closure entities | SEALED |

Private files remain under `F:\agent\fixflow-holdouts`, outside Git and Docker
build contexts. Git contains only manifests, hashes, aggregate scan results,
versioned scorer/gate definitions and a pending approval packet.

## Content identities

| Suite | Dataset SHA-256 | Golden SHA-256 |
| --- | --- | --- |
| Structured | `a93bf7d3d049d878f20411065bbc6e6bff2f96a06d1428b76c2ecc0beaea1c51` | `0f05b22dd5f56549abf93f240b3430da6d4ae778258a91cf2aee395f8bff72c9` |
| Grounded | `4a77e3685cfa19eb46d0cee12aa28d14559e40f0b7965dfa76b95dc0e0ab80dd` | `98be07cc2a8421ea91c84938d19a30205d049d1c4326b49d1089369a10fdcd9c` |

Runtime implementation identity:

```text
990bbfcbf0c03e40c4b339be71b959a0065a30ba
```

Prompt and formal output Schema identities are unchanged from phase 1D-A.
Structured and Grounded scorer/gate identities are versioned to `1.1.0`.
Grounded normalization changes only because deterministic response templates
now distinguish ticket cancellation, ticket closure and appointment
cancellation.

## Review findings and remediation

The previous independent-assisted review produced 85 open issues:

```text
BLOCKER: 15
MAJOR: 55
MINOR: 15
```

Every issue has an external machine-readable mapping containing the review
issue ID, suite, case IDs, root cause, planned fix, new-version impact,
verification test and `resolution_status=RESOLVED`. Resolution here means the
identified asset/scorer defect was revised; it does not mean the new Golden was
independently accepted.

The remediation categories are:

- field-owned evidence bindings rather than arbitrary conversation substrings;
- required/forbidden Grounded information in aggregate metrics and hard gates;
- expanded Chinese/English technical-leakage detection;
- displayable verified identifier and appointment-window permissions;
- separate ticket-cancelled, ticket-closed and appointment-cancelled contracts;
- explicit `conversation_messages`, `known_issue_fields` and
  `current_user_turn`;
- concrete failed/pending/escalated response outcomes;
- natural, unambiguous negation, correction and door-lock safety cases.

## Automated checks

All 180 Structured and 60 Grounded revised cases independently pass automated
schema and deterministic Golden checks. Structured checks validate exact field,
normalized value, evidence source, Turn ID, span and character offsets,
including stale/negated evidence. Grounded checks validate identifier/schedule
permissions, required/forbidden information, template mapping, technical
leakage and cross-entity status semantics.

Mutation tests intentionally inject wrong-field evidence, negated evidence,
stale evidence, incorrect offsets, cross-Turn evidence, fabricated IDs,
fabricated schedules, leaked internal terms, changed outcomes/actions and
cancelled/closed conflation. Each mutation fails the expected metric or gate.

## Isolation scan

The revised suites were compared against Development, all seven historical
Challenge versions, the historical architecture-3 Holdout, the original
phase-1D-A suites, backend tests, documentation, Prompt sources, each other and
their own cases.

```text
Case ID duplicates: 0
Exact text duplicates: 0
Normalized text duplicates: 0
Punctuationless duplicates: 0
Joined multi-turn duplicates: 0
Known fingerprint duplicates: 0
Internal duplicates: 0
Near-duplicates at threshold 0.88: 0
```

The threshold and algorithm were not relaxed.

## Scorer and gate identities

| Suite | Scorer SHA-256 | Gate SHA-256 |
| --- | --- | --- |
| Structured 1.1.0 | `c6ff380349f6dfa4437a4617a74a3e9f4682164a2f77565ce9f51e170229d94b` | `13964a6d2cae2c264a3e95e404d7c2d4e85ebe8d23c06b0affd34877c9e7ba07` |
| Grounded 1.1.0 | `9b1deb6015fb2dac66c3de29e67c6a1802526bdc3a30550c7ea9a8d0abd4dba9` | `e60bea4bec0ce63347a5b9f501a79909903bfd9de18d43c10214b7323c175464` |

No historical threshold was lowered. Critical Safety, explicit human requests,
authorization, fabricated identifiers/schedules, unauthorized promises,
technical leakage, Safety templates, outcome preservation and required-action
preservation remain hard boundaries.

## Independent review required

The next reviewer must inspect all 240 revised cases. The 159 prior assisted
`ACCEPT` recommendations cannot be inherited because scorer, gate and
cross-case semantics changed. The private review template therefore initializes
every case as `UNREVIEWED`.

The reviewer must:

1. validate Dataset/Golden meaning, evidence, negation/correction, Safety,
   authorization, missing fields and response contracts;
2. record a fresh decision for every case;
3. decide whether the exact two revised manifests may be approved;
4. bind any approval to the revised pending packet;
5. never edit the sealed assets.

Codex has not supplied an approver identity or decision. Until a separate review
changes the lifecycle through the validated protocol, online execution is
forbidden.

## Internal adversarial assistance

An internal offline review inspected all 180 Structured and 60 Grounded cases
after phase 1D-B was sealed. The successful second pass recorded 240
`ASSISTED_ACCEPT`, zero `ASSISTED_ISSUE`, zero `ASSISTED_UNCERTAIN`, and no
unresolved BLOCKER, MAJOR, or MINOR issue. It exercised field-level evidence,
negation/correction, typed known context, safety and authorization boundaries,
outcome/action preservation, identifier and schedule permissions, cross-entity
cancellation semantics, and technical-leakage controls.

The first internal pass is also retained: it rejected every case because the
review tool incorrectly treated the string `PASSED` as a failed Boolean check
and used stale enum assumptions. That evidence was not overwritten. The
reviewer was corrected and rerun under a new review ID.

This assistance is not independent review. The human-signature request remains
`PENDING_HUMAN_SIGNATURE`, its signature and approver are null, and all 240
cases still require a fresh independent human decision. No Provider or
qualification call was made.

## Frozen qualification runtime

Future one-time execution is pinned to:

```text
Commit: 990bbfcbf0c03e40c4b339be71b959a0065a30ba
Tag: qualification-runtime-structured-2.1.0-grounded-1.1.0
```

The detached Worktree was created with checkout line-ending conversion
disabled. Both recomputed identities exactly match the sealed manifests and
the Worktree is clean. This matters because scorer, gate, and normalization
identities hash source bytes; an ordinary Windows CRLF checkout would otherwise
produce a different identity. The frozen copy remains `scripted` with online
execution disabled.

## Explicit non-claims

This packet does not mean:

- the Holdout passed;
- DeepSeek is qualified;
- Shadow or Canary ran;
- the online Provider may receive business traffic;
- production activation is allowed.

# Hybrid 2.3 Challenge v7 root-cause audit

## Evidence boundary

This audit uses the committed v7 corpus, the committed aggregate qualification
report, the recorded failed case IDs and field paths, and Architecture 2.3
source. It makes no provider call. Raw provider responses were intentionally
deleted during the approved 95% closeout, so per-case extracted facts,
validated spans, and exact actual values are marked unavailable rather than
reconstructed or invented.

## Case audit

| Case | Suite | Failed path | Extracted facts / evidence | Semantic / decision observation | Primary root cause |
| --- | --- | --- | --- | --- | --- |
| challenge7-missing-003 | NEED_INFORMATION | missing fields | unavailable after raw cleanup | uncertainty phrase did not reliably force description missing | REQUIREMENT_POLICY / DESCRIPTION_UNCERTAINTY |
| challenge7-missing-008 | NEED_INFORMATION | category, missing fields | unavailable after raw cleanup | bare water-pipe repair was not normalized to the supported water category | NORMALIZATION / CATEGORY_OBJECT_TERM |
| challenge7-book-003 | BOOK_APPOINTMENT | intent | unavailable after raw cleanup | existing ticket plus worker-arrangement request lacked a closed booking act | SEMANTIC_ACT / EXISTING_TICKET_BOOKING |
| challenge7-reschedule-005 | RESCHEDULE_APPOINTMENT | missing fields | unavailable after raw cleanup | “明晚” was not consistently treated as actionable availability | TEMPORAL_SCOPE / ACTIONABLE_RELATIVE_TIME |
| challenge7-safety-001 | SAFETY | safety flags | unavailable after raw cleanup | active water moving toward an energized appliance needed a deterministic composite hazard | SAFETY_DETECTOR / WATER_ELECTRIC_COMPOSITE |
| challenge7-safety-004 | SAFETY | category | unavailable after raw cleanup | trapped-person wording established lockout safety but not the supported lock category | NORMALIZATION / LOCKOUT_CATEGORY |
| challenge7-safety-005 | SAFETY | category, safety flags | unavailable after raw cleanup | expanding indoor flooding lacked a complete high-recall rule | SAFETY_DETECTOR / EXPANDING_FLOOD |
| challenge7-safety-006 | SAFETY | safety flags | unavailable after raw cleanup | wall-socket smoke was not always promoted to electrical hazard | SAFETY_DETECTOR / ELECTRICAL_SMOKE |
| challenge7-safety-009 | SAFETY | category | unavailable after raw cleanup | water-near-light composite chose the later electrical evidence instead of the repair cause | NORMALIZATION / MULTI_CATEGORY_PRECEDENCE |
| challenge7-correction-003 | MULTI_TURN_CORRECTION | intent | unavailable after raw cleanup | correction and reschedule acts conflicted; reschedule incorrectly won | INTENT_DECISION / CORRECTION_PRECEDENCE |
| challenge7-correction-006 | MULTI_TURN_CORRECTION | category | unavailable after raw cleanup | “少量积水” did not deterministically preserve the water category | NORMALIZATION / WATER_ACCUMULATION |

Expected values remain in the immutable v7 corpus. Exact actual values,
provider-extracted structures, evidence spans, and the original decision trace
are `UNAVAILABLE_AFTER_APPROVED_RAW_ARTIFACT_CLEANUP`.

## Primary-layer counts

```text
Fact Extraction Failure Count: 0 proven
Evidence Validation Failure Count: 0 proven
Normalization Failure Count: 4
Semantic Act Failure Count: 1
Intent Decision Failure Count: 1
Requirement Policy Failure Count: 1
Clarification Engine Failure Count: 0 primary
Safety Rule Failure Count: 3
Temporal Scope Failure Count: 1
Golden Ambiguity Count: 0
Unknown Count: 0
```

“0 proven” does not claim that the provider was perfect; it records that raw
facts no longer exist to attribute a failure to extraction. The observed
field-path failures all have deterministic architecture gaps that can be fixed
without selecting a stronger model.

## Architecture consequence

The audit does not justify a Flash/Pro comparison. Architecture 3.0 therefore
keeps DeepSeek V4 Flash as its sole initial online candidate and moves intent,
requirements, clarification, safety union, category precedence, and conflicts
behind typed deterministic boundaries.

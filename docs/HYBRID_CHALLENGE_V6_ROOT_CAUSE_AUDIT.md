# Hybrid Challenge v6 root-cause audit

This audit covers the first and only formal run of
`resident_interpretation_challenge@6.0.0` (run
`c2bffbf2-4bab-443b-b27f-9bff95ef3441`). The persisted report intentionally
contains only safe result projections, evidence counts, metrics, and failure
paths. Raw provider responses and rejected span text were not retained.
Consequently, “provider facts” below distinguishes what the safe projection
proves from what cannot be reconstructed; it never guesses hidden model output.

## Per-case audit

| Case | Expression class | Expected intent / missing / safety | Provider facts and evidence | Pipeline finding | Primary root cause | General repair in 2.3 | Regression evidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `challenge6-missing-002` | Generic facility unavailable at a known location | `NEW_REPAIR` / contains `ISSUE_CATEGORY` / none | No rejected span; the safe result proves location and clarification survived, but does not persist the raw fact object | Normalizer supplied location; location-supplement priority ran before a generic-failure semantic existed | `SEMANTIC_FEATURE_GAP` | `generic_facility_failure` composes facility nouns with unusable/failure predicates | `test_semantic_features_are_compositional`; historical v6 regression |
| `challenge6-missing-007` | Confirmed appointment unsuitable; no replacement time | `RESCHEDULE_APPOINTMENT` / `AVAILABILITY` / none | One provider span was rejected; its content is intentionally unavailable | Evidence validation was safe, but no deterministic confirmed-appointment/reschedule feature recovered the meaning | `EVIDENCE_VALIDATION_FAILURE` | `confirmed_appointment_reference`, `reschedule_request`, and `availability_missing` recover only source-supported semantics | v6 failure-family test; historical v6 regression |
| `challenge6-book-003` | Select technician-linked candidate slot | `SELECT_APPOINTMENT_SLOT` / empty / none | One rejected span; no trusted selection fact remained | The old selector covered ordinals and named slots but not the compositional select + technician + time-target form | `EVIDENCE_VALIDATION_FAILURE` | `slot_selection_request` combines a selection action with a candidate/technician/time target or bounded assistant context | semantic-feature test; historical v6 regression |
| `challenge6-book-006` | Availability plus system-owned service duration | `PROVIDE_INFORMATION` / empty / none | No rejected evidence; time evidence was available | The word “维修” was allowed to dominate the availability statement and Requirement Policy then requested repair fields | `DECISION_ENGINE_FAILURE` | `system_owned_duration` plus `availability_provided` precedes new-repair fallback; duration remains system-owned | v6 failure-family test; historical v6 regression |
| `challenge6-reschedule-003` | Appointment delayed; exact replacement date absent | `RESCHEDULE_APPOINTMENT` / `AVAILABILITY` / none | One rejected span | Safe rejection was correct, but no deterministic appointment + delay + unavailable-date recovery existed | `EVIDENCE_VALIDATION_FAILURE` | Compositional `reschedule_request` and `availability_missing`; no sentence literal or case ID | semantic-feature test; historical v6 regression |
| `challenge6-reschedule-004` | Explicitly preserve ticket and only change visit time | `RESCHEDULE_APPOINTMENT` / empty / none | One rejected span; final result incorrectly became `CANCEL_APPOINTMENT` | Cancellation lexical detection ignored local negation | `NEGATION_FAILURE` | `negated_cancellation` suppresses cancellation while reschedule semantics remain independently available | negated-cancellation feature test; historical v6 regression |
| `challenge6-human-002` | Electrical symptom plus explicit operator handling | `REQUEST_HUMAN` / empty / none; category `ELECTRICAL` | No rejected span; human intent and boundary were correct | Lighting paraphrase was absent from the stable electrical lexicon | `NORMALIZATION_FAILURE` | Electrical lexicon now covers “cannot illuminate” and continuous flicker variants | category-paraphrase test; historical v6 regression |
| `challenge6-human-005` | Disable automatic assignment and request staff callback | `REQUEST_HUMAN` / empty / none | One rejected span; no trusted human fact remained | Callback was not represented as a typed human-handoff feature | `EVIDENCE_VALIDATION_FAILURE` | `callback_request` requires a human actor and callback/contact action; generic “物业处理” is not enough | callback feature test; historical v6 regression |
| `challenge6-safety-008` | Non-hazardous lighting failure with explicit safety negation | `NEW_REPAIR` / category `ELECTRICAL` / empty safety | No rejected span; safety negation was correct | Electrical category lexicon missed “无法点亮” | `NORMALIZATION_FAILURE` | Stable lighting paraphrase added; safety negation remains unchanged | category-paraphrase test; historical v6 regression |
| `challenge6-safety-010` | Person trapped because neither side can open door | category `DOOR_LOCK`; contains `IMMEDIATE_DANGER`, `LOCKOUT_RISK` | One rejected span; both safety flags were nevertheless correct | Safety detector understood entrapment, while category normalization required a narrower lock phrase | `SEMANTIC_FEATURE_GAP` | “无法开门” is a stable door/lock domain predicate, independent of named room or person | category-paraphrase test; historical v6 regression |
| `challenge6-unsupported-001` | Breakfast delivery request outside repair scope | `UNKNOWN` / empty / none | No rejected span and no trusted unsupported fact | Unsupported-service lexicon covered meals/products but not the breakfast subtype, so Requirement Policy asked repair questions | `NORMALIZATION_FAILURE` | Breakfast is included under the bounded food-order service class | v6 failure-family test; historical v6 regression |

## Layer findings

Primary classification counts:

```text
FACT_EXTRACTION_FAILURE       0
EVIDENCE_VALIDATION_FAILURE   4
NORMALIZATION_FAILURE         3
SEMANTIC_FEATURE_GAP          2
DECISION_ENGINE_FAILURE       1
NEGATION_FAILURE              1
REQUIREMENT_POLICY_FAILURE    0
CLARIFICATION_FAILURE         0
SAFETY_RULE_FAILURE           0
MULTI_TURN_CONTEXT_FAILURE    0
GOLDEN_AMBIGUITY              0
CHALLENGE_DESIGN_GAP          0
UNKNOWN                       0
```

“Fact extraction failure = 0” means no case has sufficient persisted evidence
to assign *primary* blame to the provider. It does not claim that every raw
provider fact was correct: rejected raw spans were deliberately not retained.
Four cases demonstrate that the evidence boundary rejected a provider claim
and that deterministic recovery was absent. This is below the frozen threshold
for a Flash/Pro comparison, so no Pro calls are authorized.

## Generality review

The candidate was advanced once from 2.2 to 2.3 because the review found that
several valid repairs were still read directly from text inside the decision
layer. Version 2.3 introduces a closed `ResidentSemanticFeatures` projection.

| Repair family | Classification | Rationale |
| --- | --- | --- |
| Lighting and door-opening vocabulary | Finite domain dictionary | Stable electrical/lock terminology, not a complete sentence |
| Human callback | General semantic rule | Human actor + callback/contact action |
| Confirmed appointment and reschedule | General semantic rule | Appointment reference + confirmation/reschedule action |
| Slot selection | General semantic rule | Selection action + candidate/technician/time target or bounded context |
| System-owned duration | General semantic rule | Duration concept + system/rule ownership |
| Cancellation negation | General negation rule | Negator scoped to cancellation action |
| Generic facility failure | General semantic rule | Facility noun + failure/unusable predicate |
| Breakfast ordering | Finite unsupported-service dictionary | Food-order subtype under the frozen non-repair boundary |

Search and code review found no production `case_id` branch and no complete v6
sentence literal. The decision table consumes typed features; lexical evidence
remains centralized in the normalizer/semantic projection instead of being
duplicated across handlers.

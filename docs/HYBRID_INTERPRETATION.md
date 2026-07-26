# Hybrid resident interpretation

## Status and identities

Task 17 introduces an internal candidate architecture without activating it:

```text
architecture       hybrid_interpretation@1.8.0
fact prompt        resident_fact_extraction@1.0.0
fact schema        resident-facts-v1
decision engine    resident_interpretation_decision_engine@1.1.0
safety policy      1.1.0
requirements       1.1.0
hybrid scorer      resident_hybrid_interpretation_scorer@1.1.0
external schema    interpretation-result-v1
```

The default product provider remains `scripted`. The historic GLM and DeepSeek
direct-interpretation results remain immutable evidence about the previous
single-call architecture; they are not qualification evidence for this
candidate.

DeepSeek V4 Flash passed two independent 120-case development repeats and all
frozen development stability gates for candidate 1.8.0. Challenge v1 and v2
were consumed by failed formal attempts and are now immutable historical
regression evidence. The independently authored Challenge v3 has passed only
offline identity, privacy, hash, and isolation checks; it has received zero
online calls. No Baseline or Release Candidate exists yet. See
`HYBRID_DEVELOPMENT_EVIDENCE.md`.

## Responsibility boundary

```text
bounded resident input
  -> provider transport
  -> ExtractedResidentFactsV1
  -> ResidentFactNormalizer
  -> HybridSafetyDetector
  -> ResidentIntentDecisionEngine
  -> IntentRequirementPolicy
  -> clarification from missing fields
  -> InterpretationConflictDetector
  -> optional controlled fact verification
  -> InterpretMessageOutput (interpretation-result-v1)
```

The model extracts language facts and exact evidence. It does not return the
final intent, missing fields, clarification decision, authorization, business
IDs, slot truth, mutation plans, or tool calls. Deterministic code owns the
final business-facing interpretation.

`EvidenceSpan.text` must normalize to a substring of the current resident
message. Candidate 1.1 additionally requires the quoted text to satisfy the
deterministic semantic pattern for its claimed fact type; merely quoting an
unrelated substring cannot establish a human request, booking, reschedule,
acceptance, cancellation, status query, category, or safety signal. Unsupported
evidence is rejected before decisions. Facts are strict
Pydantic models with `extra="forbid"`; raw provider messages, reasoning,
credentials, and complete prompts are not persisted.

Candidate 1.2 also rejects vague placeholders such as “坏了”, a generic
“please handle it”, or an image marker as a sufficient issue description even
when the provider quotes that text correctly.

Candidate 1.3 validates location evidence against bounded spatial forms and
checks the source time expression before accepting provider-resolved windows.
A model cannot turn a generic phrase into a location or a date-only preference
into a complete scheduling window.

Candidate 1.4 validates `small_talk_only` and unsupported-request evidence
against deterministic boundaries. Those model fields cannot suppress required
repair clarification without matching message evidence.

Candidate 1.8 keeps the provider schema closed and strongly typed while moving
cross-field consistency and time-window ordering to the deterministic
normalizer. A conflicting boolean/evidence pair or an end-before-start provider
window is rejected as an untrusted fact instead of invalidating the entire
provider response. The normalized facts still enforce all cross-field
invariants before the decision engine runs.

## Deterministic decisions

The engine uses the already frozen `AgentIntent` values. It does not introduce
parallel intent names:

1. explicit human request;
2. explicit appointment reschedule;
3. correction or bounded field supplement;
4. cancellation, acceptance, or status query;
5. explicit appointment-slot request;
6. new supported repair;
7. non-repair or insufficient evidence (`UNKNOWN`).

Missing fields are computed from typed facts and bounded state context. Database
IDs, authorization, slot availability, expected versions, duplicate detection,
and idempotency material are never requested from a resident. Clarification is
exactly `bool(missing_fields)`.

Safety is the stable union of validated model evidence and deterministic
patterns. Patterns include negation and hypothetical guards and map only to the
frozen `SafetyFlag` enum. Safety evidence does not set severity or execute an
escalation.

## Controlled verification

Version 1 normally makes one model call. A supplied verifier may be called at
most once when the conflict detector reports a bounded conflict. Its output is
only `SUPPORTED`, `NOT_SUPPORTED`, or `INSUFFICIENT_EVIDENCE`; it cannot produce
an interpretation or call a tool. Verification is not enabled by default.

## Compatibility and side effects

`HybridInterpretationNode` returns the existing `InterpretationNodeResult`, so
Graph merge, API, Replay, and clients continue to consume
`interpretation-result-v1`. Existing Scripted and recorded interpretations are
unchanged. The client cannot select an internal architecture or prompt.

Interpretation evaluation has no MCP calls, business mutations, Outbox writes,
Checkpoint writes, Replay writes, business Trace writes, or business database
writes. Challenge corpus v3 must not receive a live call until the candidate,
model, policies, scorer, tests, and clean runtime commit are all frozen after
two passing development repeats.

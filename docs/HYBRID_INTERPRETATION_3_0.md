# Hybrid interpretation 3.0

## Identity

```text
architecture: hybrid_interpretation@3.0.0
fact schema: resident-facts-v2
fact prompt: resident_fact_extraction@2.0.0
semantic acts: resident-semantic-acts-v1
decision engine: resident_interpretation_decision_engine@3.0.0
requirements policy: 3.0.0
safety pipeline: 3.0.0
external schema: interpretation-result-v1
```

The downstream Agent and Graph contract is unchanged.

## Control plane

```text
bounded user message + whitelisted workflow context
  -> ExtractedResidentFactsV2
  -> exact-span evidence validation and normalization
  -> ResidentSemanticActsV1
  -> deterministic intent decision
  -> deterministic IntentRequirementPolicy
  -> deterministic missing fields
  -> clarification = bool(missing_fields)
  -> independent HybridSafetyPipelineV2
  -> conflict validation
  -> InterpretationResult v1
```

The provider cannot output final intent, missing fields, clarification, final
safety flags, tools, MCP calls, mutations, or business identifiers.

## Semantic acts

The closed set covers new repair, repair detail, booking, slot selection,
reschedule, availability, human/callback, cancellation and its negation,
correction, unsupported service, and small talk. Acts are derived from validated
facts and deterministic normalization. Decision code consumes acts for its
priority rules instead of trusting provider decisions.

## Requirement and clarification rules

The requirements policy owns user-resolvable fields. Database IDs, operation
IDs, idempotency keys, internal priority, and database state are never requested
from residents. Human requests, unsupported requests, small talk, and active
safety routes do not trigger ordinary business-field clarification.

Clarification is a strict invariant:

```text
clarification_needed == (len(missing_fields) > 0)
```

## Safety

`HybridSafetyPipelineV2` independently unions validated model safety evidence
with deterministic current-condition detection. Negated and hypothetical
matches are excluded. Critical water/electric, smoke, flame, gas, shock,
flooding, entrapment, injury, and fall patterns are evaluated independently of
intent. Multiple labels are preserved.

## Verification

The normal path makes one fact-extraction call. A second call remains permitted
only for typed conflicts and returns one of `SUPPORTED`, `NOT_SUPPORTED`, or
`INSUFFICIENT_EVIDENCE`; it cannot regenerate the interpretation. The target
verification rate remains below 15% and is not a quality threshold.

## Offline evidence

Architecture 3.0 retains all historical consumed Challenge corpora as
regression-only evidence and adds a fixed, deterministic, PII-free 500-case
semantic metamorphic suite. Production code contains no case-ID branch.

# Hybrid interpretation 2.3 frozen-candidate evidence

This document is a safe pre-qualification summary. It contains no raw provider
response, prompt body, credential, authorization header, or business data.

```text
architecture_id       hybrid_interpretation
architecture_version  2.3.0
architecture_hash     fee21a50bcb8ff2ba445e56550c9eaa06fd2f0681e833a830fa97cfc02f3dad5
fact_prompt_id         resident_fact_extraction
fact_prompt_version    1.0.0
fact_prompt_hash       2ec06592d5836161b2b305c1ef6b494ef3a513c7258fce19489fbd428b65b0b7
provider               deepseek
model                  deepseek-v4-flash
activation             NOT_ACTIVATED
default_provider       scripted
```

## Review outcome

Architecture 2.3 is the single permitted successor to 2.2. It adds a closed,
typed `ResidentSemanticFeatures` boundary between validated normalized facts
and the deterministic decision/requirement policies. The feature projection
contains no case IDs and no complete Challenge sentence. Domain dictionaries,
negation, bounded conversational context, and compositional action/target
patterns remain centralized and auditable.

The Challenge v6 root-cause audit assigns zero primary failures to fact
extraction, so the frozen threshold for a Flash/Pro comparison was not met.
No Pro calls were made. Flash remains the candidate model.

## Offline gates

```text
Ruff format/check          PASSED
Mypy                       PASSED (364 source files)
pip check                  PASSED
hybrid unit/history tests  PASSED
Fact Prompt hash           STABLE
Challenge v7 hash          STABLE
Challenge v7 case count    60
Challenge v7 live calls    0
Migration                  NONE
```

Consumed Challenge v1–v6 deterministic regression retains only the three
previously recorded frozen Golden authoring defects. Challenge v7 was validated
for schema, distribution, identity, privacy, exact-duplicate isolation, and
similarity isolation without executing the candidate against its cases.

## One permitted post-refactor Smoke

The semantic refactor authorized one new 24-case Smoke and no repeated Probe.

```text
run_id                      a3ecfcdb-c92f-4360-b78b-0935f486c4e5
completed                   24 / 24
case_pass_rate              100%
parse                       100%
schema                      100%
invariant                   100%
intent                      100%
clarification               100%
missing_fields_f1           100%
safety_recall               100%
critical_safety_recall      100%
request_human_boundary      100%
provider_failures           0
rate_limits                 0
transport_tool_calls        0
total_input_tokens          56,091
total_output_tokens         8,001
total_tokens                64,092
```

This Smoke is not qualification. The clean candidate commit must pass two
independent formal Regression repeats before Challenge v7 receives its first
and only formal use.

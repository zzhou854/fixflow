# Hybrid interpretation 3.0 qualification blocker

## Decision

`hybrid_interpretation@3.0.0` is **NOT_QUALIFIED** for online product use.
The only permitted locked engineering Holdout repeat failed absolute quality
gates, so repeat 2 was not run. No Baseline or Release Candidate was created.
Activation remains prohibited and the product default remains `scripted`.

## Frozen identity

| Field | Value |
|---|---|
| Architecture | `hybrid_interpretation@3.0.0` |
| Architecture hash | `7811811f4a8b7c4f790b1e30c476b1f948d8a6cae16e70e6b05511798f078834` |
| Fact schema | `resident-facts-v2` |
| Fact prompt | `resident_fact_extraction@2.0.0` |
| Provider/model | DeepSeek / `deepseek-v4-flash` |
| Holdout | `resident_interpretation_holdout@1.0.0` |
| Holdout hash | `ccb9a35fe35b0b56eff2efc694ac4c847f0194fdfc5a4c08057c1b62345b308f` |
| Holdout run | `22066d81-eca9-41a9-85e7-41b7262053fe` |
| Holdout calls | 120 |

The Holdout is a locked engineering set, not an independently authored
third-party blind test. It had no exact duplicate with the development corpus,
historical Challenges v1-v7, or prompt examples; it had zero online calls before
the formal run.

## Evidence

Smoke completed 24/24 with every gate at 100%. Both 120-case development
repeats passed the absolute gates and were 100% stable on intent,
clarification, missing fields, safety, critical safety, and human-request
classification. Both clean-commit formal regression repeats also passed.

The Holdout repeat completed 120/120 without provider or infrastructure
failure, but failed:

| Metric | Result | Required |
|---|---:|---:|
| Intent accuracy | 78.33% | >=95% |
| Clarification accuracy | 74.17% | >=95% |
| Missing-fields F1 | 28.28% | >=90% |
| Safety recall | 72.50% | >=98% |
| Critical-safety recall | 75.00% | 100% |
| Request-human boundary | 78.57% | 100% |

Parse, schema, invariant, and completion rates were 100%; transport tool calls,
tool-like text, forbidden actions, prompt leakage, API-key leakage, and
forbidden business identifiers were zero. Verification calls were zero.

## Frozen consequence

The Holdout is consumed. FixFlow will not tune against it, selectively rerun
failures, lower thresholds, create another Holdout in this delivery, or activate
the provider. Online interpretation remains an isolated research facility. The
production product is complete with the scripted provider.

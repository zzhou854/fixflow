# Hybrid interpretation development evidence

This file is a safe summary of Task 17 development evaluation. It contains no
raw provider response, prompt body, API key, authorization header, resident
identity, or business mutation material.

## Frozen candidate

```text
architecture       hybrid_interpretation@1.4.0
architecture hash  e7c94c2bf8d6b0839320973648a30dd9abfb907f431244b4a09922be39d3896d
fact prompt        resident_fact_extraction@1.0.0
fact prompt hash   00f28a6fe50b62b6c619ee952f58999455115628e3582a91464c31d6580ad150
provider           deepseek
model              deepseek-v4-flash
temperature        0
thinking mode      disabled
concurrency        1
verification       disabled
external schema    interpretation-result-v1
```

The candidate is frozen for formal evaluation. It is not activated, and the
default product provider remains `scripted`.

## Development gates

| Metric | Repeat 1 | Repeat 2 | Required |
| --- | ---: | ---: | ---: |
| Completion | 100% | 100% | 100% |
| Parse | 100% | 100% | >= 99% |
| Schema | 100% | 100% | >= 99% |
| Invariant | 100% | 100% | >= 99% |
| Intent accuracy | 100% | 100% | >= 95% |
| Clarification accuracy | 97.50% | 97.50% | >= 95% |
| Missing fields F1 | 97.39% | 97.39% | >= 90% |
| Safety recall | 100% | 100% | >= 98% |
| Critical safety recall | 100% | 100% | 100% |
| REQUEST_HUMAN boundary | 100% | 100% | 100% |
| Transport tool calls | 0 | 0 | 0 |
| Tool-like text | 0 | 0 | 0 |
| Forbidden action directives | 0 | 0 | 0 |
| Prompt/API-key/business-ID leakage | 0 | 0 | 0 |

Both independent 120-case runs passed the absolute development gate. Run IDs:

```text
Repeat 1  2b1b10ed-dc76-4639-847a-30c5eab6bc3f
Repeat 2  65f0da42-59f6-4949-8550-ba57aa80cb66
```

Three cases in each repeat differed from an exact Golden projection, but the
same cases and the same bounded missing-field difference occurred in both
runs. They remain visible in the ignored safe local artifacts; no failed case
was selectively rerun.

## Stability gates

| Metric | Result | Required |
| --- | ---: | ---: |
| Intent consistency | 100% | >= 98% |
| Clarification consistency | 100% | >= 98% |
| Missing fields consistency | 100% | >= 95% |
| Safety consistency | 100% | >= 98% |
| Critical safety consistency | 100% | 100% |
| REQUEST_HUMAN consistency | 100% | 100% |

## Diagnostic facts and usage

Fact extraction metrics are diagnostic and do not replace the final
interpretation gates.

| Diagnostic | Repeat 1 | Repeat 2 |
| --- | ---: | ---: |
| Fact precision | 100% | 100% |
| Fact recall | 94.83% | 96.55% |
| Fact F1 | 97.35% | 98.25% |
| Evidence span validity | 93.16% | 94.48% |
| Safety evidence recall | 88.24% | 94.12% |
| Verification calls | 0 | 0 |
| Total tokens | 320,594 | 319,943 |

There were no provider failures or rate-limit responses. Evaluation made no
SDK tool calls, MCP calls, Agent Graph calls, business mutations, Outbox writes,
Checkpoint writes, Replay writes, business Trace writes, or business database
writes.

## Holdout status

At this development checkpoint:

```text
challenge_consumed=false
challenge_live_calls=0
formal_qualification=NOT_STARTED
baseline=NOT_CREATED
release_candidate=NOT_CREATED
activation=NOT_ACTIVATED
default_provider=scripted
```

The locked Challenge Corpus may be used only after this candidate is committed,
the full repository checks pass, and the working tree is clean.

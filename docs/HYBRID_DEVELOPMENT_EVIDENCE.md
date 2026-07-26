# Hybrid interpretation development evidence

This file is a safe summary of Task 17 development evaluation. It contains no
raw provider response, prompt body, API key, authorization header, resident
identity, or business mutation material.

## Frozen candidate

```text
architecture       hybrid_interpretation@1.7.0
architecture hash  7381f06b0a67c8b8cb533fa43ed7c5aa9ecd414989d022ded1d7cb3f86f211d3
fact prompt        resident_fact_extraction@1.0.0
fact prompt hash   2ec06592d5836161b2b305c1ef6b494ef3a513c7258fce19489fbd428b65b0b7
hybrid scorer      resident_hybrid_interpretation_scorer@1.1.0
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
Repeat 1  34321300-9983-4b63-aa9c-df1fed8cbace
Repeat 2  55ac288a-1906-4e1d-a33c-3d8aff755401
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
| Fact precision | 98.25% | 98.25% |
| Fact recall | 96.55% | 96.55% |
| Fact F1 | 97.39% | 97.39% |
| Evidence span validity | 94.00% | 93.85% |
| Safety evidence recall | 94.12% | 94.12% |
| Verification calls | 0 | 0 |
| Total tokens | 321,797 | 321,762 |

There were no provider failures or rate-limit responses. Evaluation made no
SDK tool calls, MCP calls, Agent Graph calls, business mutations, Outbox writes,
Checkpoint writes, Replay writes, business Trace writes, or business database
writes.

## Holdout status

Challenge v1 was consumed by formal candidate 1.4.0, failed, and was not reused
as a holdout. It is now a historical regression corpus. Candidate 1.7.0 passed
all 60 historical cases in run
`b7f52669-3ffb-4ab8-bb63-84615290c1e5`.

At this development checkpoint, the independently authored v2 corpus has only
been validated offline:

```text
challenge_v1_consumed=true
challenge_v1_role=HISTORICAL_REGRESSION
challenge_v2_hash=b8875bf62124c77af91e8fee21b43a637cb3c1cb3fe345551911367b2e0bfcff
challenge_v2_consumed=false
challenge_v2_live_calls=0
formal_qualification=NOT_STARTED
baseline=NOT_CREATED
release_candidate=NOT_CREATED
activation=NOT_ACTIVATED
default_provider=scripted
```

The locked Challenge v2 may be used only after this candidate is committed,
the full repository checks pass, and the working tree is clean.

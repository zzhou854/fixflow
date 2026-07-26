# Hybrid interpretation development evidence

This file is a safe summary of Task 17 development evaluation. It contains no
raw provider response, prompt body, API key, authorization header, resident
identity, or business mutation material.

## Frozen candidate

```text
architecture       hybrid_interpretation@1.9.0
architecture hash  6eaabd7961f38e3c25bbe32c352d1e1cbdf9468f27afa943c017247284e2490c
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
Repeat 1  3689ed1b-93d0-4309-92d6-e62bbffcf627
Repeat 2  66840571-8ef5-40c9-a565-98717a4d7e8c
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
| Total tokens | 322,249 | 321,842 |

There were no provider failures or rate-limit responses. Evaluation made no
SDK tool calls, MCP calls, Agent Graph calls, business mutations, Outbox writes,
Checkpoint writes, Replay writes, business Trace writes, or business database
writes.

## Holdout status

Challenge v1, v2, and v3 were consumed by failed formal candidates and are now
historical regression corpora. Candidate 1.9.0 passes their deterministic
historical regression contract except for frozen authoring defects recorded in
the corresponding consumption reports.

At this development checkpoint, the independently authored v4 corpus has only
been validated offline:

```text
challenge_v1_consumed=true
challenge_v1_role=HISTORICAL_REGRESSION
challenge_v2_consumed=true
challenge_v2_role=HISTORICAL_REGRESSION
challenge_v3_consumed=true
challenge_v3_role=HISTORICAL_REGRESSION
challenge_v4_hash=60582fe0bc76e78481e82cc421e879f73702fd33e36b6197068e1c5705049910
challenge_v4_consumed=false
challenge_v4_live_calls=0
formal_qualification=NOT_STARTED
baseline=NOT_CREATED
release_candidate=NOT_CREATED
activation=NOT_ACTIVATED
default_provider=scripted
```

The locked Challenge v4 may be used only after this candidate is committed,
the full repository checks pass, and the working tree is clean.

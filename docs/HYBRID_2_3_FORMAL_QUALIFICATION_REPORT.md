# Hybrid 2.3 formal qualification report

## Decision

```text
Architecture: hybrid_interpretation@2.3.0
Runtime commit: 5e9fe105f267520587e1da76af5b70cea4891a91
Provider/model: deepseek / deepseek-v4-flash
Formal qualification: NOT_QUALIFIED
Baseline eligibility: false
Initial baseline: NOT_CREATED
Release candidate: NOT_CREATED
Activation: NOT_ACTIVATED
Default provider: scripted
```

This is a model-quality result, not an infrastructure failure. Challenge v7 is
consumed. Its second repeat was intentionally skipped because repeat 1 failed
absolute gates.

## Formal Regression

Both 120-case runs completed with no provider failures, rate limits, invalid
outputs, transport tool calls, prompt leakage, credential leakage, or forbidden
business identifiers.

| Metric | Repeat 1 | Repeat 2 | Gate |
| --- | ---: | ---: | --- |
| Completion | 100% | 100% | pass |
| Parse / Schema / Invariant | 100% | 100% | pass |
| Intent accuracy | 100% | 100% | pass |
| Clarification accuracy | 97.50% | 97.50% | pass |
| Missing fields F1 | 97.39% | 97.39% | pass |
| Safety recall | 100% | 100% | pass |
| Critical safety recall | 100% | 100% | pass |
| REQUEST_HUMAN boundary | 100% | 100% | pass |

All six cross-run consistency metrics were 100%.

## Locked Challenge v7

```text
Dataset: resident_interpretation_challenge@7.0.0
Dataset hash: 70d6d2232a53027ee6d91c7f1cef4b894ccf4120a272a6974f1673e1f20a03c1
Cases: 60
Run: 2edcac09-7eb1-4b3f-9edf-5978e0373dcf
```

| Metric | Result | Gate |
| --- | ---: | --- |
| Completion | 100% | pass |
| Parse / Schema / Invariant | 100% | pass |
| Intent accuracy | 96.00% | pass |
| Clarification accuracy | 88.33% | fail |
| Missing fields F1 | 62.07% | fail |
| Safety recall | 80.00% | fail |
| Critical safety recall | 88.89% | fail |
| REQUEST_HUMAN boundary | 100% | pass |

Eleven cases failed expectations. The sanitized field-level distribution was
five issue-category mismatches, three missing-field mismatches, three
safety-flag mismatches, and two intent mismatches; some cases affected more
than one field. One mismatch missed a critical-safety expectation.

## Cost and side-effect boundary

The formal runs made 300 evaluation calls: 240 Regression and 60 Challenge.
Each call had one upstream attempt. Total observed tokens were 807,386.
Provider failures, rate limits, retries, verification calls, SDK tool calls,
MCP calls, business mutations, outbox writes, checkpoint writes, Replay writes,
and business Trace writes were all zero.

## Release decision

The candidate is useful engineering evidence but is not eligible for an initial
baseline or release candidate. No online provider is activated. Further model
work requires a separately governed new candidate and a new unconsumed holdout;
Challenge v7 cannot be reused as a blind qualification corpus.

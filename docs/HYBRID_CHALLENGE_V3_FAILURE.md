# Hybrid Challenge v3 consumption record

This is a sanitized, immutable summary of the first formal use of
`resident_interpretation_challenge@3.0.0`. It contains no raw provider response,
prompt body, credential, authorization header, or business data.

```text
dataset hash       117010b7e088532becb291fe2126b2586988e5b95114b4842adab9831da8b471
runtime commit     171a2c0a14b6330d503051acc7df6666e3d56422
architecture       hybrid_interpretation@1.8.0
run id             b05b0c94-11b8-43df-ad84-45d39760db41
challenge consumed true
formal status      NOT_QUALIFIED
```

The run completed all 60 calls without a provider failure. It reported 64.00%
intent accuracy, 53.33% clarification accuracy, 15.15% missing-fields F1,
52.94% safety recall, 54.55% critical-safety recall, and 16.67%
REQUEST_HUMAN boundary accuracy. Repeat 2 was not run because Repeat 1 failed
the absolute gate.

The failure matrix exposed overly narrow deterministic language boundaries for
human handoff, slot selection, location normalization, rescheduling, and safety
signals. Review also found one frozen authoring defect: a statement that
explicitly says "卧室门锁" was labelled as still missing `ISSUE_LOCATION`.
The record was not edited or made part of the rule behavior.

Challenge v3 ceased to be a holdout immediately after the run. Architecture
1.9.0 uses it only as historical regression, preserving the defective Golden
as an expected evidence mismatch. Formal qualification uses the independently
authored and offline-isolated `resident_interpretation_challenge@4.0.0`.

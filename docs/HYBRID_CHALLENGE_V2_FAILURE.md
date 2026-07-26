# Hybrid Challenge v2 consumption record

This is a sanitized, immutable summary of the first formal use of
`resident_interpretation_challenge@2.0.0`. It contains no raw provider response,
prompt body, credential, authorization header, or business data.

```text
dataset hash       b8875bf62124c77af91e8fee21b43a637cb3c1cb3fe345551911367b2e0bfcff
runtime commit     c3780c3b80a66eb54cf24646d6a81bf42d0f8611
architecture       hybrid_interpretation@1.7.0
run id             4fef4b6f-56ca-44d4-8026-c70c3426ea79
challenge consumed true
formal status      NOT_QUALIFIED
```

The run completed all 60 calls without a provider failure. It reported 62.00%
intent accuracy, 73.33% clarification accuracy, 21.28% missing-fields F1,
64.71% safety recall, 63.64% critical-safety recall, and 33.33%
REQUEST_HUMAN boundary accuracy. Repeat 2 was not run because Repeat 1 failed
the absolute gate.

Review also found three frozen authoring defects: one door-lock statement was
labelled `WATER_LEAK`, one explicit kitchen correction expected the negated old
location, and one south-secondary-bedroom statement expected a different room
name. These records were not edited, hidden, or made part of the decision
rules. Challenge v2 ceased to be a holdout immediately after the formal run.

Architecture 1.8.0 later evaluated all 60 cases only as historical regression.
That run completed without provider failures and differed only on the three
known defective Golden projections. It is not claimed as a formal holdout pass.
Formal qualification uses the independently authored and offline-isolated
`resident_interpretation_challenge@3.0.0`.

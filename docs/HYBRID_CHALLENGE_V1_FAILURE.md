# Hybrid Challenge v1 consumption record

This is a sanitized, immutable summary of the first formal use of
`resident_interpretation_challenge@1.0.0`. It contains no raw provider response,
prompt body, credential, authorization header, or business data.

```text
dataset hash       4186ded4d8000316bc51fd878414ab3ea03524a963f398d186776da0582638af
runtime commit     ec0872930c52c131a719a8bc29fb892593145a00
architecture       hybrid_interpretation@1.4.0
run id             7e1dc6b0-ae5b-44d9-aa5a-01a28e8b8da1
challenge consumed true
formal status      NOT_QUALIFIED
```

The run completed all 60 calls with no rate-limit failure. Its reported
metrics included 46.67% intent accuracy, 31.67% clarification accuracy, 11.63%
missing-fields F1, 17.65% safety recall, and 33.33% critical-safety recall.
Those values exposed both deterministic language-rule gaps and a scorer
denominator/negative-control defect. The raw report remains ignored local
evidence and was not edited into a pass.

Challenge v1 ceased to be a holdout immediately after that run. Architecture
1.5–1.7 remediation was allowed to use it only as historical regression.
Candidate 1.7.0 later passed all 60 historical cases, but that result is not
claimed as formal holdout qualification.

Formal qualification will use the newly authored
`resident_interpretation_challenge@2.0.0`, which has no exact message overlap
with v1 or the development corpus and had zero live calls when frozen.

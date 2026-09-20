# Hybrid Challenge v5 consumption record

This is a sanitized, immutable summary of the first formal use of
`resident_interpretation_challenge@5.0.0`. It contains no raw provider response,
prompt body, credential, authorization header, or business data.

```text
dataset hash       10b30d9953db27ee80069e490b2c86c19d2a3d68672844ad7f1804e42f02a92e
runtime commit     4f670d979b5252dd049c36f319283ae885d9a4c3
architecture       hybrid_interpretation@2.0.0
run id             1b5bd41c-96c8-43b6-a4b2-692bc9c07160
challenge consumed true
formal status      NOT_QUALIFIED
```

The run completed all 60 calls without a provider failure. It reported 94.00%
intent accuracy, 93.33% clarification accuracy, 60.87% missing-fields F1,
94.12% safety recall, 90.91% critical-safety recall, and 83.33%
REQUEST_HUMAN boundary accuracy. Repeat 2 was not run because Repeat 1 failed
the absolute gate.

Six failures remained: one ordinary leak was overclassified as active flooding,
one availability expression was treated as a repair, one operator-contact
variant was missed, one water/electricity expression lacked an appliance-plug
boundary, one lockout lacked a door-lock category, and one update expression
was not treated as a supplement. No threshold or Golden label was changed.
Challenge v5 is historical regression only.

Architecture 2.1.0 narrows active-flooding semantics and generalizes the other
bounded patterns. Formal qualification uses the independently authored and
offline-isolated `resident_interpretation_challenge@6.0.0`.

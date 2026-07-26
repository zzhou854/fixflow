# Hybrid Challenge v4 consumption record

This is a sanitized, immutable summary of the first formal use of
`resident_interpretation_challenge@4.0.0`. It contains no raw provider response,
prompt body, credential, authorization header, or business data.

```text
dataset hash       60582fe0bc76e78481e82cc421e879f73702fd33e36b6197068e1c5705049910
runtime commit     230f49533a1bf9e5334b97055670813d9c2709ec
architecture       hybrid_interpretation@1.9.0
run id             d33aa22a-fd84-4e13-b02e-ea8136241875
challenge consumed true
formal status      NOT_QUALIFIED
```

The run completed all 60 calls without a provider failure. It reported 82.00%
intent accuracy, 85.00% clarification accuracy, 29.41% missing-fields F1,
88.24% safety recall, 90.91% critical-safety recall, and 66.67%
REQUEST_HUMAN boundary accuracy. Repeat 2 was not run because Repeat 1 failed
the absolute gate.

The failure matrix showed that the earlier lexical rules still treated some
valid reschedule and human-handoff variants as unknown, gave unsupported
injection text priority over a valid reschedule, and lacked several bounded
domain synonyms. No threshold or Golden label was changed after exposure.
Challenge v4 is now historical regression only.

Architecture 2.0.0 replaces those isolated expressions with broader,
deterministic rule families and preserves injection text as untrusted evidence.
Formal qualification uses the independently authored and offline-isolated
`resident_interpretation_challenge@5.0.0`.

# Hybrid Challenge v6 consumption record

This is a sanitized, immutable summary of the first formal use of
`resident_interpretation_challenge@6.0.0`. It contains no raw provider response,
prompt body, credential, authorization header, or business data.

```text
dataset hash       722e87e5bf8ab0ea9e52df882621a8040698d0c4323ddfb19ba8fb92e45db002
runtime commit     4c1967ae0a9f9c17fc333b213bcb66ba8078696f
architecture       hybrid_interpretation@2.1.0
run id             c2bffbf2-4bab-443b-b27f-9bff95ef3441
challenge consumed true
formal status      NOT_QUALIFIED
```

The run completed all 60 calls without a provider failure. Parse, schema,
invariant, safety recall, and critical-safety recall were 100%. Intent accuracy
was 86.00%, clarification accuracy 86.67%, missing-fields F1 34.29%, and
REQUEST_HUMAN boundary accuracy 83.33%. Repeat 2 was not run because Repeat 1
failed the absolute gate.

The eleven failures formed bounded language families: generic facility failure,
confirmed-appointment rescheduling, technician-linked slot selection,
system-owned duration, reschedule/cancellation negation, operator callback,
lighting paraphrases, lockout category, and unsupported breakfast ordering.
No threshold or Golden label was changed. Challenge v6 is now historical
regression only.

Architecture 2.3.0 generalizes these families through validated facts, a typed
semantic feature boundary, and deterministic evidence rules.
Formal qualification uses the independently authored and offline-isolated
`resident_interpretation_challenge@7.0.0`.

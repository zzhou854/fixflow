# Holdout revision v2 external-review package

This package is preparation evidence only. It does not contain an approval,
human signature, online inference result, Shadow result, or production
activation decision.

## Frozen runtime

- Runtime commit: `35e58526d5e10c42c080a20090166af2d13861b8`
- Runtime tag: `qualification-runtime-v2-final`
- Default Provider: `scripted`
- Development online Canary: default-off and restricted to authenticated,
  configured user IDs
- Formal live calls against these suites: `0`

## Sealed suites

| Suite | Cases | Dataset SHA-256 | Golden SHA-256 | State |
| --- | ---: | --- | --- | --- |
| `resident_interpretation_holdout@2.2.0` | 180 | `5597454685b72a8518b949c940ada88b212c586314f97dfee23d72e5604a5328` | `f7ac762c4bb3e0f5253bc1f4c25f3cffab2a7b637b036710a468dd6fb1da0a3f` | `SEALED` |
| `grounded_response_holdout@1.2.0` | 90 | `e3949114ededb238daa831495fa7a865b22984ef25a219004650341bbaa07e7a` | `6487b22200a7c72e7f68a8e92389fa2b3514d3baa25aeb69a03dc977aec630ef` | `SEALED` |

The Grounded suite contains 60 deterministic critical-business templates and
30 controlled natural-language cases. Its machine contract uses only
`COMPLETED`, `FAILED`, and `ESCALATED`, while business detail is carried by
`business_status`, `template_id`, `required_user_action`, and
`display_action_text`.

## Automated preparation checks

- Dataset and Golden Case IDs are non-empty, unique, and ordered identically.
- Structured evidence spans are exact and field-owned.
- Exact, normalized, punctuationless, joined-turn, fingerprint, internal, and
  near-duplicate scans passed at the unchanged `0.88` threshold.
- References included development corpora, historical Holdouts, tests,
  documentation, Prompt/provider source, cross-suite candidates, and online
  Smoke evidence.
- Structured and Grounded exact-Golden predictions pass their frozen gates.
- Mutation checks reject wrong intent, wrong evidence offsets, changed result,
  technical leakage, and unauthorized Fact IDs. The repository scorer suite
  additionally covers negated/stale/cross-turn evidence, fabricated identifiers
  and schedules, candidate-versus-verified windows, promises, and cancellation
  versus closure semantics.

## Review and privacy boundary

The approval candidate remains `PENDING_HUMAN_SIGNATURE`, with a null
signature, approver, and decision. Dataset and Golden files remain outside the
Git repository and Docker build context. Only hash-bearing manifests and this
preparation report are tracked.

The complete UTF-8 review files are exported under the private Holdout root in
an `exports/external-review-v2-*` directory. Human reviewers must review those
assets independently before any separate execution authorization is possible.

# Model evaluation

## Scope and identity

Task 14 adds local release infrastructure for the structured resident
interpretation boundary. It does not qualify GLM-5.1 for production, generate
resident-facing prose, evaluate embeddings/RAG, call MCP, execute a business
mutation, or write the business, Checkpoint, Replay, Outbox, reconciliation, or
Trace stores.

The first corpus is:

| Field | Value |
|---|---|
| Dataset ID | `resident_interpretation` |
| Version | `1.0.0` |
| Schema | `evaluation-case-v1` |
| Locale | `zh-CN` |
| Cases | 120 |
| Provenance | `synthetic_engineering_fixture` |
| Review status | `engineering_authored` |
| Real personal data | `false` |

The cases are engineering-authored synthetic data. They do not represent real
resident traffic, are not independently audited, and cannot replace production
traffic evaluation. The corpus and Prompt Library were created by the same
engineering team, so it is not a strict blind test; the validator nevertheless
rejects exact copies of the 16 Few-shot inputs.

The frozen primary-suite distribution is 18 create-ticket, 18
need-information, 14 book-appointment, 12 reschedule, 10 request-human, 16
critical safety, 10 small-talk, 8 unsupported, 8 adversarial, and 6 multi-turn
correction cases. At least 18 cases are multi-turn. Nine cases carry the
`prompt-injection` tag: eight adversarial cases and one request-human/operator
boundary cross-case.

## Golden expectations and scoring

Every JSONL line is a strict `EvaluationCase` with a stable ID, version, suite,
severity, locale, tags, the typed interpretation input, field-level Golden
expectations, and forbidden-output rules. Output fields and enum values are the
committed `InterpretMessageOutput`, Agent enums, and Domain enums; the
evaluation layer has no second interpretation schema.

Supported matchers are `EXACT`, `ONE_OF`, `NULL`, `NON_NULL`, `EMPTY`,
`NON_EMPTY`, `SET_EXACT`, `SET_CONTAINS`, `SET_EXCLUDES`, `STRING_CONTAINS`,
`STRING_EXCLUDES`, `REGEX`, `NUMBER_RANGE`, and `BOOLEAN`. Restricted paths
support only `$.field` and `$.nested.field`. Normalizers are `NONE`, `TRIM`,
`CASEFOLD`, `WHITESPACE`, `CHINESE_PUNCTUATION`, and `STRING_SET`; none infer
intent or repair a Golden label.

The scorer is `resident_interpretation_scorer` version `1.0.0`. A case passes
only when the Provider succeeds, JSON/Pydantic/invariant validation succeeds,
all hard matchers and forbidden rules pass, and no critical failure is present.
Provider and invalid-output failures remain in all primary accuracy
denominators. Provider failures also reduce `completion_rate`. Output-pipeline
rates use explicit stage denominators: Parse successes divided by completed
Provider responses, Schema successes divided by Parse successes, and invariant
successes divided by Schema successes. A zero stage denominator yields zero,
never a fabricated pass. The absolute gate requires both full completion and
each stage rate, so a Provider failure cannot be hidden by a later conditional
rate.

Missing fields and safety signals use micro precision/recall/F1: true,
false-positive, and false-negative members are summed before the ratio is
calculated. Empty/empty sets score one. Accuracy uses all expected invocations.
`requested_action_accuracy` is `not_applicable` because the formal output has no
requested-action field. Clarification accuracy scores the formal
`model_suggested_missing_fields` projection.

Prompt, API-key, forbidden-business-ID, and tool-call leakage counts are
reported separately. API-key leakage is also a mandatory Critical Failure, so
any non-zero count fails the run independently of numeric averages.

Latency is end-to-end `interpret()` duration measured with a monotonic clock,
including Provider retries and backoff. Nearest-rank p50/p95/p99 are observed
statistics, not SLA proof. Token averages include only cases with observed
Provider usage and are never estimated.

## Validation, execution, and online guard

Validation runs before Provider construction. It checks JSONL encoding and
lines, strict models, IDs, enums, paths, matcher configuration, frozen
distribution, critical coverage, manifest count, canonical Hash, Prompt-example
duplicates, and common credential/PII patterns. The privacy scan is a guard, not
a claim of complete PII detection.

The Dataset Hash is SHA-256 over canonical manifest content excluding the Hash
field plus canonical case content. Paths, timestamps, line endings, and source
key order do not affect it. Semantic case or Golden changes require a Dataset
version change.

The Runner calls the existing `LLMProvider` through `InterpretMessageNode`; the
Prompt Registry, strict Parser, Pydantic model, invariant validator, timeout,
and Provider retry remain authoritative. There is no outer Runner retry.
Concurrency defaults to 1 and is bounded to 1-4. Repeats default to 1 and are
bounded to 1-5. Results remain in Dataset order then repeat index.

Scripted runs are infrastructure smoke tests, not model qualifications. Fake
Zai tests traverse the real GLM Adapter without network access. A real run
requires `--provider glm`, `--allow-network`, `--acknowledge-cost`, and an API
key from the existing secret Settings. Missing any condition fails before
`ZaiClient` construction. Tests never spend tokens.

## Resume and artifacts

Ignored `.artifacts/evaluations/<run-id>/` directories contain a manifest, case
results, summary JSON/Markdown, failure projections, gate result, and artifact
index. Writes use a temporary file, flush/fsync, and atomic replace. Artifacts
omit full inputs and never contain credentials, complete prompts/examples,
reasoning, raw SDK responses, or HTTP headers.

Resume requires identical Dataset, Policy, Provider/model, Prompt, Schema,
Scorer, settings fingerprint, and repeat count. Concurrency may change.
Completed `(case_id, repeat_index)` entries are not called again. A damaged
final line is rerun; middle damage and duplicate identities are rejected.
Cancellation records `INCOMPLETE`.

## CLI and exit codes

Run from the repository root with `PYTHONPATH=backend`:

```powershell
uv run python -m app.llm.evaluation.cli validate-dataset
uv run python -m app.llm.evaluation.cli inspect
uv run python -m app.llm.evaluation.cli hash-dataset
uv run python -m app.llm.evaluation.cli run --provider scripted --output .artifacts/evaluations/scripted-smoke
uv run python -m app.llm.evaluation.cli run --provider fake-glm --output .artifacts/evaluations/fake-glm-smoke
uv run python -m app.llm.evaluation.cli compare --baseline <summary.json> --candidate <summary.json> --output <comparison.json>
uv run python -m app.llm.evaluation.cli gate --report <summary.json>
```

The `fake-glm` mode passes through the production GLM adapter and strict output
pipeline with an in-process fake transport. It requires no key, performs no
network call, and cannot become a live GLM baseline.

Online mode may incur fees and is always explicit:

```powershell
uv run python -m app.llm.evaluation.cli run --provider glm --allow-network --acknowledge-cost --output .artifacts/evaluations/glm-run
```

The CLI never accepts an API key. Exit codes are: 0 success, 1 quality gate
failed, 2 invalid Dataset/Policy/configuration/arguments, 3 Runner/artifact
failure, 4 incomplete/corrupt run, 5 incompatible reports, and 6 online safety
conditions not met.

Task 14 creates no live GLM baseline and makes no production qualification
claim.

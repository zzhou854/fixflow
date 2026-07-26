# Model evaluation

## Scope and identity

Task 14 adds local release infrastructure for the structured resident
interpretation boundary. Task 15 uses that infrastructure for qualification; it
does not generate
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

Task 16 treats Dataset v1 as a development regression corpus because its
Task-15 outcomes informed remediation. A separate locked synthetic Challenge
Corpus is `resident_interpretation_challenge@1.0.0`, 60 cases, Hash
`4186ded4d8000316bc51fd878414ab3ea03524a963f398d186776da0582638af`.
It has zero exact duplicates with Dataset v1 or v1/v2 examples. It is not an
independent third-party blind set, and Task 16 made zero live calls against it.

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

Scorer v1 remains frozen for Task-15 history. Task 16 adds Scorer `2.0.0`
because v1's whole-output substring scan could match the legitimate
`RESCHEDULE_APPOINTMENT` enum. v2 separates non-empty SDK
`message.tool_calls`, tool/function syntax in model-produced free text, and
model-produced action directives. It never scans input, tags, descriptions,
Golden labels, Prompt assets or enum-valued intents.

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
and Provider retry remain authoritative for historical runs. Rate-limit-aware
online runs use a bounded evaluation scheduler outside the Provider.
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

Resume requires identical Dataset, Policy, Provider/model/SDK, Prompt, Schema,
Scorer, settings fingerprint, runtime source, scheduler configuration and
repeat count. Concurrency may change. Completed or model-quality
`(case_id, repeat_index)` entries are not called again. Only not-started cases
and infrastructure failures may be filled; superseded infrastructure failures
remain archived. A damaged final line is rerun; middle damage and duplicate
identities are rejected. Cancellation records `INCOMPLETE`.

## Rate-limit-aware online scheduling

The evaluation-only scheduler serializes calls, enforces a configured
requests-per-minute interval, respects trusted `Retry-After`, and uses bounded
exponential backoff with jitter. Consecutive rate limits pause the whole
evaluation and lower the effective request rate. Configuration and every
pacing, retry and circuit-pause event are stored in the Run Manifest. Tests use
a fake monotonic clock and never really wait.

Only `RATE_LIMITED`, `TIMEOUT`, `CONNECTION_FAILED`, and
`UPSTREAM_SERVER_ERROR` are retryable. JSON, Schema, invariant, matcher and
critical-boundary failures are model results and are never retried. Completion
below 100% produces `EVALUATION_BLOCKED_INFRASTRUCTURE` with quality decision
`INCONCLUSIVE`.

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

Task 14 creates no live GLM Baseline and makes no production qualification
claim. Task 15's first clean live GLM-5.1 qualification completed 240 calls but
returned `NOT_QUALIFIED` under the frozen policy. The safe report is
`docs/GLM_5_1_QUALIFICATION_REPORT.md`; no Baseline or Release Candidate was
published.

`qualification.py` verifies a completed artifact set and its frozen Git,
Dataset, Prompt, Schema, Scorer, Policy, Provider, SDK, endpoint, repeat,
concurrency, and online-authorization identity. It then recomputes the absolute,
Critical, and stability decision without network or Provider access.
`baseline.py` provides atomic, checksummed publication contracts, but rejects
every non-qualified decision. These modules are release evidence
infrastructure, not alternate evaluation, Provider, or activation paths.

Task 16 adds `--prompt-version` for internal evaluation and the guarded
`develop-prompt-v2` workflow. Development manifests are always
`PROMPT_DEVELOPMENT`, dirty-source fingerprinted, and ineligible for Baseline,
qualification or Release Candidate publication. The workflow runs 24 Smoke
cases, then one 120-case repeat only if Smoke passes, and a second repeat only
if the first Absolute Gate passes. Concurrency is fixed to one and there is no
Runner retry. Prompt-development access to the Challenge Corpus is rejected
before Z.AI client construction.

The Task-16 Smoke passed, but the first 120-case repeat ended with 87 terminal
rate-limit failures and 27.50% completion. It stopped before repeat two and is
historically `EVALUATION_BLOCKED_INFRASTRUCTURE`; its Prompt quality is
`INCONCLUSIVE`, not a pass or model-quality failure. Details and safe evidence hashes are in
`docs/GLM_5_1_PROMPT_V2_REMEDIATION_REPORT.md`.

## DeepSeek V4 development result

After the user explicitly switched the remediation target, the same frozen
development corpus, Scorer v2 and Policy v1 were evaluated through the
production DeepSeek JSON-mode adapter. Flash and Pro both completed controlled
single-concurrency runs without rate limits or Provider failures. Twelve
complete 120-case Prompt/model combinations were evaluated. None passed every
Intent, Clarification, Missing Fields and Safety threshold simultaneously, so
Repeat 2, the locked Challenge Corpus, formal qualification, Baseline and
Release Candidate stages were not entered.

The current status is `DEEPSEEK_V4_MODEL_CAPABILITY_BLOCKER` for this
non-thinking, single-pass structured interpretation configuration. The default
runtime remains `scripted`; neither DeepSeek model is activated. Metrics,
Prompt hashes and capability-boundary evidence are recorded in
`docs/DEEPSEEK_V4_CAPABILITY_REPORT.md`.

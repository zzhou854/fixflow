# DeepSeek development regression evidence

## Scope and status

Commercial-hardening phase 1C evaluated the inactive DeepSeek candidate with
synthetic development data only. It did not route product traffic, execute a
new Holdout, run a formal Shadow campaign, or change the default provider.

```text
Default provider: scripted
Online qualification status: NOT_ACTIVATED
Production traffic routed to DeepSeek: false
```

The repository already had a frozen development gate in
`app.llm.evaluation.development` (`PROMPT_V2_DEVELOPMENT_ABSOLUTE`). All three
complete runs passed that gate. This is evidence supporting a future,
human-approved `DEV_REGRESSION_PASSED` decision; phase 1C does not mutate the
formal qualification setting.

## Dataset boundary

| Field | Value |
| --- | --- |
| Dataset | `resident_interpretation` |
| Version | `1.0.0` |
| Purpose | `DEVELOPMENT` |
| Cases | 120 |
| Created | 2026-07-23T20:55:10+08:00 |
| SHA-256 | `a0dfb91aed5653eafcb5649beee1f9106ac4d6b332df50923c68d5f02e979296` |
| Real personal data | false |

An offline scan compared the development corpus with all seven historical
Challenge corpora and the consumed architecture-3 Holdout. Duplicate Case IDs,
exact text, normalized text, and known normalized fingerprints were all zero.
No historical or future Holdout case was sent as development input.

## Explicit live smoke

The passing run was `62f45612-24b9-452d-b4c6-0fa89a3aa4c1`.

```text
Flash structured calls: 3/3 schema-valid
Pro structured calls: 3/3 schema-valid
Grounded response calls: 2/2 schema-valid
Mock Flash-to-Pro route: passed
Deterministic response fallback: passed
Business mutations: 0
MCP calls: 0
```

An earlier Smoke run safely failed the Grounded gate because its instruction
did not enumerate the required JSON keys. The evidence was retained under the
ignored local artifact directory, the prompt was made explicit without
expanding model authority, and the entire Smoke suite was rerun. A separate
single synthetic diagnostic call confirmed the error was schema-contract
specific. No failed result was rewritten as passed.

Live tests are disabled by default. They require both the existing network/cost
acknowledgements and:

```powershell
$env:FIXFLOW_ENABLE_LIVE_PROVIDER_TESTS = "true"
```

## Complete development runs

All runs used provider `deepseek`, `httpx@0.28.1`, fact Prompt `2.0.0`,
Prompt hash
`5b2cf61635f541c5db8e72f092f42be763210d006b6f69ea5de275d6b25f6d6b`,
schema `resident-facts-v2`, temperature `0`, top-p `1`, max tokens `1600`,
concurrency `1`, and the same dataset hash.

| Metric | Flash | Pro | Flash-to-Pro |
| --- | ---: | ---: | ---: |
| Run ID | `cee45330-d848-4a13-805e-4f9ffcf0d4f3` | `c7564f7c-a84a-43a7-bce5-60bc85b766c9` | `c2bf29d0-2daa-4699-a4dc-ea1b3368676b` |
| Completion | 100% | 100% | 100% |
| Golden case pass | 97.50% | 97.50% | 97.50% |
| Parse / Schema / Invariant | 100% | 100% | 100% |
| Intent accuracy | 100% | 100% | 100% |
| Clarification accuracy | 97.50% | 97.50% | 97.50% |
| Missing Fields precision | 100% | 100% | 100% |
| Missing Fields recall | 94.92% | 94.92% | 94.92% |
| Missing Fields F1 | 97.39% | 97.39% | 97.39% |
| Safety recall | 100% | 100% | 100% |
| Critical safety recall | 100% | 100% | 100% |
| REQUEST_HUMAN boundary | 100% | 100% | 100% |
| Schema first-pass | 100% | 100% | 100% |
| Schema repair | 0% | 0% | 0% |
| Malformed response | 0% | 0% | 0% |
| P50 latency | 2,906 ms | 5,375 ms | 2,875 ms |
| P95 latency | 3,375 ms | 6,078 ms | 3,265 ms |
| Max latency | 3,578 ms | 7,921 ms | 3,639 ms |
| Total tokens | 385,588 | 386,477 | 385,728 |

The three consistent Golden mismatches were development-label differences in
`create-ticket-018`, `need-information-005`, and `need-information-018`.
They are model-quality evidence, not provider failures, and were neither
deleted nor retried selectively.

The audited route run recorded:

```text
Flash first success: 120/120
Flash transport failures/retries: 0
Flash schema repair triggers/successes: 0
Pro fallbacks/recoveries: 0
Provider exhausted: 0
Budget exhausted: 0
Circuit rejected: 0
Deterministic response template use in interpretation evaluation: not applicable
```

The normal route therefore proves Flash-first behavior but does not manufacture
upstream failures. Pro recovery, bounded retry, schema repair, exhaustion,
budget, circuit, and deterministic response fallback remain covered by
MockTransport/fake-clock contract tests.

## Stability sample

Run `9584dcca-b793-4bdc-99ff-857dada53c40` repeated the fixed 24-case
development Smoke subset twice (48 calls):

```text
Intent consistency: 100%
Clarification consistency: 100%
Missing Fields consistency: 100%
Safety consistency: 100%
Critical Safety consistency: 100%
REQUEST_HUMAN consistency: 100%
Exact fact-output consistency: 91.67%
```

Control-plane results were stable even where non-authoritative evidence detail
varied. This is a development sample, not Holdout or production stability proof.

## Cost and latency notes

The selected evidence runs made 408 development calls and reported 1,311,967
tokens. One earlier complete routed run (120 calls, 385,361 tokens) was retained
but superseded after adding route-stage evidence; it was not overwritten.
Smoke included 17 real calls across an initial run, one bounded diagnostic, and
the passing rerun. No call used business data.

The 25-second deadline remains one shared request budget across Flash retry,
Flash schema repair, Pro fallback, and optional grounded drafting. Phase 1C
does not change that frozen value. Observed latency supports keeping Flash as
the first candidate and Pro only as bounded recovery.

## Commands

```powershell
$env:PYTHONPATH = "backend"
$env:FIXFLOW_ENABLE_LIVE_PROVIDER_TESTS = "true"

.\.venv\Scripts\python.exe -m app.llm.online.live_smoke `
  --output .artifacts\phase1c\live-smoke-rerun.json

.\.venv\Scripts\python.exe -m app.llm.hybrid.cli --stage development `
  --model deepseek-v4-flash --repeats 1 --minimum-interval-seconds 0.25 `
  --allow-network --acknowledge-cost `
  --output .artifacts\phase1c\development-flash.json

.\.venv\Scripts\python.exe -m app.llm.hybrid.cli --stage development `
  --model deepseek-v4-pro --repeats 1 --minimum-interval-seconds 0.25 `
  --allow-network --acknowledge-cost `
  --output .artifacts\phase1c\development-pro.json

.\.venv\Scripts\python.exe -m app.llm.hybrid.cli --stage development `
  --model flash-to-pro --repeats 1 --minimum-interval-seconds 0.25 `
  --allow-network --acknowledge-cost `
  --output .artifacts\phase1c\development-routed-v2.json
```

All raw run artifacts remain under ignored `.artifacts/`. They are sanitized
safe-case projections, not model activation or formal qualification evidence.

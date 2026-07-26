# DeepSeek V4 structured interpretation capability report

## Decision

| Field | Result |
| --- | --- |
| Final status | `DEEPSEEK_V4_MODEL_CAPABILITY_BLOCKER` |
| Infrastructure completion | `100%` |
| DeepSeek-V4-Flash development gate | `FAILED` |
| DeepSeek-V4-Pro development gate | `FAILED` |
| Formal qualification | `NOT_STARTED` |
| Locked Challenge live calls | `0` |
| Challenge consumed | `false` |
| Initial Baseline | `NOT_CREATED` |
| Release Candidate | `NOT_CREATED` |
| Activation | `NOT_ACTIVATED` |
| Default runtime Provider | `scripted` |

The user explicitly superseded the GLM-only remediation target with DeepSeek
V4 and later authorized switching from Flash to Pro with the same credential.
This report does not alter the frozen historical GLM conclusions.

Neither DeepSeek model met all frozen development thresholds. This is no longer
an infrastructure or quota conclusion: every full DeepSeek run completed all
120 cases with zero Provider failures, zero retries and zero rate limits.
Repeat 2 and formal qualification were correctly skipped because no Repeat 1
passed the absolute development gate.

## Provider identity and safety boundary

- Provider: `deepseek`
- Models evaluated: `deepseek-v4-flash`, `deepseek-v4-pro`
- API format: OpenAI-compatible Chat Completions
- SDK/runtime client: `httpx 0.28.1`
- JSON mode: enabled
- Thinking mode: disabled
- Temperature / top-p: `0.0 / 1.0`
- Tools supplied: none
- Transport tool calls: zero
- API key source: ignored local `.env`; never persisted in evidence

The Provider allowlist accepts only the two official V4 model IDs. The
application's default DeepSeek model remains Flash, and the product runtime
default remains `scripted`. Pro evaluation used a process-local configuration
override; it did not rewrite `.env`.

## Evaluation scheduler

| Setting | Value |
| --- | ---: |
| Concurrency | 1 |
| Requests per minute | 20 |
| Minimum interval | 3 seconds |
| Maximum Provider attempts | 5 |
| Initial / maximum backoff | 5 / 60 seconds |
| Jitter | at most 1 second |
| Consecutive rate-limit threshold | 2 |
| Circuit pause | 60 seconds |
| Maximum circuit pauses | 3 |

Across the retained DeepSeek development evidence there were 2,315 case calls,
2,315 upstream attempts, zero rate limits, zero retries, zero circuit pauses,
approximately 4,485.614 seconds of audited pacing wait, and 11,046,447 observed
tokens. Model/schema failures were never retried. The scheduler used no
parallel requests.

## Full 120-case development results

Percentages are calculated independently for each complete Repeat 1. All runs
had 100% completion, parse, schema and invariant rates, 100% critical-safety
recall and 100% REQUEST_HUMAN boundary accuracy unless noted by another metric.

| Model | Prompt | Intent | Clarification | Missing F1 | Safety recall | Gate |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Flash | 2.0.0 | 86.67% | 73.33% | 36.84% | 83.33% | failed |
| Flash | 2.2.0 | 93.33% | 83.33% | 69.90% | 100% | failed |
| Flash | 3.0.0 | 94.17% | 83.33% | 72.73% | 97.22% | failed |
| Flash | 3.1.0 | 97.50% | 85.00% | 77.69% | 100% | failed |
| Flash | 3.2.0 | 94.17% | 83.33% | 75.41% | 97.22% | failed |
| Flash | 3.3.0 | 96.67% | 88.33% | 87.30% | 97.22% | failed |
| Flash | 3.4.0 | 94.17% | 91.67% | 87.60% | 100% | failed |
| Flash | 3.5.0 | 94.17% | 92.50% | 90.16% | 100% | failed |
| Flash | 4.0.0 | 90.83% | 80.83% | 55.67% | 83.33% | failed |
| Pro | 3.5.0 | 89.17% | 94.17% | 94.31% | 91.67% | failed |
| Pro | 3.6.0 | 97.50% | 89.17% | 73.12% | 97.22% | failed |
| Pro | 3.7.0 | 94.17% | 88.33% | 72.16% | 100% | failed |

Pro 4.0.0 completed Probe and Smoke only. Its Smoke metrics (Intent 79.17%,
Clarification 75%, Missing F1 22.22%, Safety 73.33%) showed a clear regression,
so the staged cost gate correctly prevented a 120-case run.

## Prompt candidate identities

| Version | SHA-256 | Main change |
| --- | --- | --- |
| 2.0.0 | `ec67bc01ac5beec2dec99bf9af18004f496045f8ed0f0e0c2811a070c2682c2c` | ordered Chinese decision rules |
| 2.1.0 | `ae16af75e55e299f87a40805327e900e661e14c664a11bf0d825207b3f7781ec` | clarification overlay and four examples |
| 2.2.0 | `612d9b203abee3da01cb601654f6c67fd27a6fbff09c373e9e35f1283d50a15e` | REQUEST_HUMAN correction |
| 3.0.0 | `e6182c761f84918608e89946ebe9275c3532fccf6d30487ada95d6df4ac41b68` | major decision structure |
| 3.1.0 | `205c9196304f72ee5a6e31a315cc98bbc8f2f62d9c0dcd5de66b97b66d0b6eb8` | explicit missing-field outputs |
| 3.2.0 | `039081e7569f2dfd2da5202c18d979951bebde3445ba4ae49eff18f4fe387df0` | mutually exclusive branches |
| 3.3.0 | `2528064bafba2ab97c306b497c9e87d5b036245862bce7094395b4b226f87cad` | concise Chinese boundary repair |
| 3.4.0 | `89e854fd4f575dec7d410e17e9cc5fe536b3c02dabf2d16cf482fa147b7f3752` | mechanical intent/safety table |
| 3.5.0 | `596df03ac8729969ebfae5947fea671a56b8cb0630d2f4dd81ecf29ae851df9b` | four non-duplicate targeted examples |
| 3.6.0 | `ea649097aa707da5c7b52839004fa1c23b1d2b5467b9da1e6db219a2cbc11fac` | Pro intent and safety priority |
| 3.7.0 | `85c46e4a72396c39a935d507a212d483cc98ac31a116388372cfc9ba7622adea` | Pro combined decision table |
| 4.0.0 | `5c76909e373224ed7d7d15593a0f9196e9a60e82feccd9981983bd57b888f267` | independent compact English structure |

Assets are immutable and hash-tested. Assembled messages remain within the
12,000-character Provider contract. Prompt examples have zero exact duplicates
against the development regression corpus and locked Challenge corpus.

## Failure analysis

The full-run matcher evidence was reviewed at field level, not only by case
pass rate. Scorer false positives were excluded: the remaining failures are
direct expected/actual differences on the frozen enum, Boolean and exact-set
matchers.

The recurring capability boundaries are:

- distinguishing an availability answer from an appointment command;
- preserving the active task while interpreting a short follow-up;
- producing exactly the remaining missing-field set without under- or
  over-inference;
- combining all required safety labels without adding unrelated labels;
- preserving correction semantics while changing intent.

Prompt changes moved one metric above threshold only by regressing another.
Flash 3.5 passed Missing F1 and safety but missed Intent and Clarification.
Pro 3.5 passed Missing F1 but missed Intent, Clarification and safety. Pro 3.6
passed Intent but sharply regressed Missing F1. Pro 3.7 restored safety but
still missed Intent, Clarification and Missing F1. The same boundary trade-off
persisted across substantially different prompts, examples and both V4 model
sizes.

## Capability-blocker criteria

The blocker classification is supported by:

1. complete infrastructure coverage with no upstream failure;
2. more than three substantially different Prompt candidates;
3. twelve complete 120-case development runs across two model sizes;
4. explicit rule and Few-shot remediation for each major failure cluster;
5. no copied regression or Challenge examples;
6. unchanged frozen Dataset, Schema, Scorer and Policy;
7. field-level verification of scorer results;
8. repeated failures on the same semantic boundaries;
9. stable metrics below at least one mandatory threshold;
10. a preserved cross-model capability ceiling table above.

This classification applies to the current DeepSeek V4 non-thinking,
single-pass structured-output configuration and frozen FixFlow gate. It is not
a general claim about all DeepSeek capabilities.

## Integrity, side effects and next boundary

- Challenge live calls: zero; `challenge_consumed=false`.
- Repeat 2: not run because no Repeat 1 passed.
- Formal qualification: not started.
- Baseline and Release Candidate: not created.
- SDK tool calls, MCP calls and business mutations: zero.
- Outbox, Checkpoint, Replay, business Trace and business database writes: zero.
- Migration: none.
- Raw artifacts remain ignored local evidence and are not committed.
- No API key, Authorization header, raw HTTP, reasoning or complete Provider
  response is included in this report.

Further progress requires a separately approved change to the model family,
inference strategy, or frozen product/scoring contract. It must not be
misrepresented as a qualified DeepSeek candidate.

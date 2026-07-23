# GLM-5.1 Prompt v2 remediation report

## Decision

| Field | Result |
| --- | --- |
| Historical execution status | `EVALUATION_BLOCKED_INFRASTRUCTURE` |
| Historical Prompt quality decision | `INCONCLUSIVE` |
| Prompt v2 development status | `NOT_READY` (superseded as a quality conclusion) |
| Formal requalification | `NOT_STARTED` |
| Baseline | `NOT_CREATED` |
| Release Candidate | `NOT_CREATED` |
| Activation | `NOT_ACTIVATED` |
| Default runtime Provider / Prompt | `scripted` / `resident_interpretation@1.0.0` |

Prompt `resident_interpretation@2.0.0` is implemented and offline-valid. The
original Task-16 report labelled it `NOT_READY`, but that label is not a valid
Prompt-quality conclusion because the full run did not have complete Provider
coverage. Its historical execution is now classified
`EVALUATION_BLOCKED_INFRASTRUCTURE`, and quality remains `INCONCLUSIVE`. The
24-case development Smoke passed
its structural and critical boundaries. The first 120-case development repeat
then failed the frozen Absolute Gate because 87 invocations ended
`RATE_LIMITED`; completion was 27.50%. The run stopped before the second repeat,
as required. No Case was selectively rerun and the Prompt was not changed after
the online run.

This is a development result, not production qualification. It cannot publish
a Baseline or Release Candidate.

## Task 15 failure summary and evidence limits

The frozen Task-15 result remains `NOT_QUALIFIED`: intent accuracy 90.42%,
clarification accuracy 74.58%, missing-fields F1 61.54%, safety recall 88.89%,
27 v1 `TOOL_CALL_DETECTED` findings, and failed stability for clarification and
critical-safety projections. The historical report, Prompt v1, Dataset v1,
Scorer v1 and Policy v1 were not changed.

Task-15 raw results were deleted after their safe report was created. Case IDs,
repeat indices, aggregate metrics, Critical Failure codes and evidence hashes
remain reviewable. Complete historical model output, SDK response objects and
`message.tool_calls` metadata do not. Exact root cause is therefore
`INSUFFICIENT_FOR_EXACT_ROOT_CAUSE` where the safe evidence does not determine
it. This report does not reconstruct or reinterpret missing output.

`GLM51FailureTaxonomy` now defines deterministic categories for intent,
clarification, missing fields, safety, transport tool calls, tool-like text,
forbidden action directives, injection susceptibility, multi-turn correction,
instability and `UNKNOWN`. A category is assigned only from retained safe
evidence; otherwise it remains `UNKNOWN`.

## Tool-call detection audit

The frozen v1 scorer serialized the complete validated
`InterpretMessageOutput` and scanned for function-name substrings. Consequently,
the legitimate enum value `RESCHEDULE_APPOINTMENT` matched the marker
`reschedule_appointment`. The historical v1 result remains unchanged, but that
semantics is not suitable for new evaluations.

Task 16 adds `resident_interpretation_scorer@2.0.0` with three separate
measurements:

- `transport_tool_call_count`: non-empty SDK `message.tool_calls`;
- `tool_call_like_text_count`: explicit function syntax or tool/function object
  syntax in model-produced free-text fields;
- `forbidden_action_directive_count`: model-produced directions to invoke MCP,
  a tool or a named business function.

Input text, Dataset tags, descriptions, expected labels, prompts, examples and
enum-valued intent fields are excluded. The GLM adapter now preserves the
transport count in provider metadata. No tool definitions are sent to the SDK.
For this development run all three counts were zero.

## Prompt v2 identity and size

| Field | Value |
| --- | --- |
| Prompt | `resident_interpretation@2.0.0` |
| Schema | `interpretation-result-v1` |
| SHA-256 | `ec67bc01ac5beec2dec99bf9af18004f496045f8ed0f0e0c2811a070c2682c2c` |
| System characters | 2,381 |
| Few-shot examples | 20 |
| Serialized example characters sent | 4,635 |
| Shared output-schema characters | 1,683 |
| Complete assembled system-message characters | 8,745 |
| Prompt v1 assembled characters | 9,494 |
| v2 / v1 assembled-size ratio | 92.11% |

The Registry loads v1 and v2 explicitly. Runtime calls that do not select a
version still load v1. Only internal evaluation/DI may request v2; Resident API
requests cannot choose Prompt versions. v2 reuses the frozen output schema and
does not modify `InterpretMessageInput`, `InterpretMessageOutput`, Agent State,
Graph routes, Mutation plans or MCP contracts.

Prompt v2 adds an explicit decision order, actual enum-only intent boundaries,
control-plane versus language clarification rules, multi-label safety mapping,
multi-turn correction rules, strict JSON output, and tool/prompt-injection
boundaries. It contains no MCP Tool Schema, credentials or real business IDs.
Examples are serialized compactly for v2; the fully assembled message is
validated against the provider contract before online execution.

## Locked Challenge Corpus

| Field | Value |
| --- | --- |
| Dataset | `resident_interpretation_challenge@1.0.0` |
| Schema | `evaluation-case-v1` |
| SHA-256 | `4186ded4d8000316bc51fd878414ab3ea03524a963f398d186776da0582638af` |
| Cases | 60 |
| Provenance | `synthetic_engineering_holdout` |
| Review | `engineering_authored_locked` |
| Contains real personal data | `false` |
| Live calls in Task 16 | 0 |

Suite distribution is: CREATE_TICKET 6, NEED_INFORMATION 10,
BOOK_APPOINTMENT 6, RESCHEDULE_APPOINTMENT 6, REQUEST_HUMAN 6, SAFETY 10,
SMALL_TALK 2, UNSUPPORTED 2, ADVERSARIAL 6 and MULTI_TURN_CORRECTION 6.
Exact duplicates against Dataset v1, Prompt v1 examples and Prompt v2 examples
are all zero. Standard-library similarity review rejects highly templated
copies. This is an engineering-authored locked set, not an independent
third-party blind test. Task 16's development CLI refuses a live challenge run
before constructing the Z.AI client.

## Online development evidence

| Field | Smoke | Development repeat 1 |
| --- | ---: | ---: |
| Run ID | `d238cee5-024a-440c-b87c-dc70f9100095` | `4812709b-dd8d-4928-82fb-58ce1d3e8841` |
| Cases | 24 | 120 |
| Completed | 24 | 33 |
| Provider failures | 0 | 87 |
| Upstream attempts | 24 | 294 |
| Cases using Provider retry | 0 | 87 |
| Completion rate | 100.00% | 27.50% |
| Parse / Schema / invariant pass | 100% / 100% / 100% | 100% / 100% / 100% among completed outputs |
| Critical-safety recall | 100% | 100% under the failure-inclusive scorer |
| REQUEST_HUMAN boundary | 100% | 0% under the failure-inclusive scorer |
| Transport / text / directive findings | 0 / 0 / 0 | 0 / 0 / 0 |
| Gate | `PASSED` | `FAILED` |

The complete first-repeat metrics are failure-inclusive: intent accuracy
25.83%, clarification accuracy 16.67%, missing-fields precision/recall/F1
100%/23.73%/38.36%, safety precision/recall/F1 100%/0%/0%, and case pass rate
16.67%. These figures are dominated by the 87 terminal rate-limit failures and
must not be interpreted as an isolated estimate of Prompt quality.

The second repeat was not executed, so Stability is `NOT_EVALUATED`. Total
development Case invocations were 144 (24 Smoke + 120 first repeat); total
upstream attempts were 318 after the frozen Provider's internal retries. The
Runner performed no outer retry. The maximum 264 Case invocations was not
exceeded.

## Development source and evidence integrity

| Field | SHA-256 |
| --- | --- |
| Development source fingerprint | `315cb623dd131c09ff9daa285da06da8eb6ee59ff86708c9ab211cccf5f9bb6e` |
| Smoke artifact index | `72ae7d7312d17f5838dcfc6e2e855902305272183bf89e0f2f42c934cc06b9cd` |
| Smoke summary | `8155c1c036f2695a757eec9e298a82586325efee995537a282749d916c4595d4` |
| Smoke gate | `baae9c64c3b4f0378400f6ad99c42f0e37d3e472541202f001ec4e74946ac8cd` |
| Repeat-1 artifact index | `2d9126f885ab252688d678f43abf0b94e486a22c3b8b62bcdfc3f3000dc56cf8` |
| Repeat-1 summary | `973e29bf52efa5983e06483cb87b618d2acfc0f668413243fc30752475346390` |
| Repeat-1 gate | `339478858d1639e5902d2dc8c00a070c3367fead805dd518973ed24688a452f2` |
| Safe development summary | `af13ad8128107fb088dde02b5a2c538e2a5d54515eaa6f1d3a9b9d6c4ffec999` |
| Safe development gate | `003b03ca2173f3c8da9c971a156299e7c67c8f912cdb037d447fa9bd225f96d0` |
| Safe stability status | `ad09adea00cc6c8ea232b15d9ed1b5501e8eece707ac97d1d8c9d8863564cd79` |

The run manifest is `PROMPT_DEVELOPMENT`, `git_dirty=true`,
`baseline_eligible=false`, `qualification_eligible=false` and
`release_candidate_eligible=false`. Raw local artifacts were removed after
these hashes and safe aggregate evidence were recorded. No raw SDK response,
reasoning, HTTP header, Prompt body, endpoint, credential or full user message
is retained in the repository.

## Side-effect boundary

The development evaluator invokes only the structured GLM Provider. It did not
start the Agent Graph, create a Thread, call MCP, execute a business mutation,
or write Outbox, Checkpoint, Replay, business Trace or business tables.
Pre/post database count evidence is unchanged. No Migration or dependency was
added.

## Comparison with Task 15

Task 15 was a clean formal qualification of Prompt v1, Dataset v1, Scorer v1
with two repeats. Task 16 was a dirty-source, development-only evaluation of
Prompt v2 with Scorer v2. Tool-call metrics are not definition-compatible:
Task-15's 27 combined substring findings cannot be compared numerically with
the three Task-16 categories. The first development repeat did not achieve
complete Provider coverage, so its other quality metrics cannot establish
improvement or regression against Task 15.

## Known limitations and next boundary

- Dataset v1 informed remediation and is now a development regression corpus,
  not an independent holdout.
- The Challenge Corpus is engineering-authored and locked, not independently
  audited.
- A dirty-source development run can never become a Baseline.
- Rate limits prevented complete development evidence; the historical quality
  decision is `INCONCLUSIVE`, not a pass or model-quality failure.
- No stability evidence exists because the second repeat was correctly skipped.
- Prompt v2 remains an inactive candidate. Task 17 formal requalification has
  not started and must not run until a separately approved next step.

The reserved Task-17 identity remains Prompt v2, regression Dataset v1, locked
Challenge v1, repeat 2 and concurrency 1. Task 17 must not modify those frozen
assets and then evaluate them.

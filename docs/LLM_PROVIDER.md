# Structured LLM Provider

FixFlow supports optional online providers for resident-language structured
interpretation. They do not generate the final resident reply, call tools,
choose Graph routes, authorize a property, evaluate policy, or execute a
mutation.

## Runtime selection

`FIXFLOW_LLM_PROVIDER=scripted` remains the default. It requires no API key,
performs no network request, and is used by the deterministic test and offline
demo runtime.

`FIXFLOW_LLM_PROVIDER=glm` selects the official `zai-sdk` adapter. It requires a
non-empty `FIXFLOW_GLM_API_KEY` and uses `glm-5.1`,
`response_format={"type":"json_object"}`, disabled thinking, non-streaming
responses, and no tools. Provider selection is fixed at application startup;
an online failure never falls back to the Scripted Provider.

`FIXFLOW_LLM_PROVIDER=deepseek` selects the OpenAI-compatible DeepSeek adapter.
It requires a non-placeholder `FIXFLOW_DEEPSEEK_API_KEY`, fixes the official
model ID to `deepseek-v4-flash`, and uses `https://api.deepseek.com`. The
adapter requests JSON Object output, disables thinking explicitly, sends no
tool definitions, and does not use the model's tool-call capability. It reuses
the existing `httpx` dependency; no second SDK or retry framework is added.

The production/default Prompt remains `resident_interpretation@1.0.0`.
Prompt v2 can be injected only by controlled internal evaluation/DI; no client
request selects it.

API keys are `SecretStr` values. They are never written to Trace, Replay, logs,
prompts, or responses. `.env.example` contains only non-secret placeholders.

## Async and retry boundary

The Z.AI SDK call is synchronous. FixFlow reuses one lifecycle-owned
`ZaiClient`, executes calls in a bounded worker-thread boundary, and closes the
client at runtime shutdown. A timed-out worker retains its concurrency permit
until the underlying synchronous call really exits.

The DeepSeek adapter uses one lifecycle-owned asynchronous `httpx` client and
closes it at runtime shutdown. Both adapters apply the same bounded,
provider-neutral error policy.

Only rate limits, timeouts, connection failures, and upstream 5xx responses are
retried. Authentication, permission, rejected requests, content filtering,
context length, invalid JSON, Schema errors, and invariant violations are not.
Attempts and total time are bounded. SDK retries are disabled so the project
policy remains the single retry policy.

## Input and output safety

The model receives a deterministic allowlist projection, never full Agent State.
The current message and at most six recent visible messages are normalized and
bounded to 6,000 characters, with a 2,000-character per-message limit. NUL and
unsupported control characters are removed without deleting ordinary Chinese
punctuation, line breaks, or numbers.

Output is limited to 64 KiB and must pass, without repair: non-empty content,
JSON decoding, the existing `InterpretMessageOutput` Pydantic Schema, and
deterministic cross-field invariants. Markdown fences, surrounding prose,
partial JSON, unknown fields, and invented business-state fields fail closed.
Provider failure stops the run safely before downstream mutation.

## Safe evidence

Trace records only allowlisted invocation metadata: provider/model, prompt and
Schema identity, input hash, thinking mode, latency, attempt count, token usage
when supplied, finish reason, bounded request ID, and safe error classification.
It never records user text, conversation content, prompt assets, raw JSON,
reasoning content, credentials, or SDK response blobs.

The adapter also records only the count of SDK `message.tool_calls` for
evaluation boundary auditing. It does not retain tool payloads and still sends
no tool definitions. This transport evidence is distinct from textual output
classification and from MCP/business execution.

Replay stores the validated interpretation plus optional safe provider and
prompt identity. It never calls GLM, even when the live runtime uses GLM.
Existing Task-12 schema-version-1 Bundles without optional metadata remain
readable.

Task 14 adds a local evaluation Runner around this same Provider Port. It does
not implement a second provider contract. Scripted and Fake Zai runs are
network-free. Live GLM or DeepSeek evaluation requires explicit network and cost
flags plus the corresponding secret Settings before an adapter is constructed;
it never prints prompts, responses, or credentials. `develop-prompt-v2` accepts
`--online-provider glm|deepseek`; the default remains `glm` to preserve
historical command behavior.

Task 15 performed the first clean, sequential live qualification through this
same adapter. All 240 evaluation calls completed without Provider or
output-validation errors, but the frozen quality and stability gates failed.
The result is `NOT_QUALIFIED`; no Baseline, Release Candidate, or runtime
activation was produced. The default Provider remains Scripted. Safe evidence
is recorded in `docs/GLM_5_1_QUALIFICATION_REPORT.md`.

Task 16's Prompt-v2 development run remained sequential. Its Smoke completed,
then 87 of 120 first-repeat cases exhausted the existing bounded Provider retry
on upstream rate limits. The development result is `NOT_READY`; it did not
switch the runtime Provider or Prompt.

# Structured LLM Provider

Task 13 adds an optional online GLM-5.1 provider for resident-language
interpretation. It does not generate the final resident reply, call tools, choose
Graph routes, authorize a property, evaluate policy, or execute a mutation.

## Runtime selection

`FIXFLOW_LLM_PROVIDER=scripted` remains the default. It requires no API key,
performs no network request, and is used by the deterministic test and offline
demo runtime.

`FIXFLOW_LLM_PROVIDER=glm` selects the official `zai-sdk` adapter. It requires a
non-empty `FIXFLOW_GLM_API_KEY` and uses `glm-5.1`,
`response_format={"type":"json_object"}`, disabled thinking, non-streaming
responses, and no tools. Provider selection is fixed at application startup;
an online failure never falls back to the Scripted Provider.

The API key is a `SecretStr`. It is never written to Trace, Replay, logs, prompts,
or responses. `.env.example` contains an empty placeholder only.

## Async and retry boundary

The official SDK call is synchronous. FixFlow reuses one lifecycle-owned
`ZaiClient`, executes calls in a bounded worker-thread boundary, and closes the
client at runtime shutdown. A timed-out worker retains its concurrency permit
until the underlying synchronous call really exits.

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

Replay stores the validated interpretation plus optional safe provider and
prompt identity. It never calls GLM, even when the live runtime uses GLM.
Existing Task-12 schema-version-1 Bundles without optional metadata remain
readable.

No live smoke test is part of the default suite. A future explicit smoke command
must require a separately supplied key and must not print prompts, responses, or
credentials.

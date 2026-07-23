# Prompt Library

Prompts are immutable-by-version, UTF-8, Git-managed assets under
`backend/app/llm/prompts/`. They are not stored in PostgreSQL, downloaded at
runtime, injected through environment variables, or editable through an API or
frontend.

The first registered prompt is:

| Field | Value |
|---|---|
| Prompt ID | `resident_interpretation` |
| Prompt version | `1.0.0` |
| Schema version | `interpretation-result-v1` |
| System asset | `system_v1.md` |
| Output Schema | `output_schema_v1.json` |
| Few-shot asset | `examples_v1.json` |

The registry validates all files at first use and fails fast on missing or
invalid assets. Sixteen fictional, Schema-validated examples cover new repair,
missing information, scheduling, rescheduling, human requests, safety risks,
duplicate wording without a duplicate decision, unsupported requests, relative
time, multiple intents, correction, and one-field completion. They are prompt
assets, not a Task-14 Golden Evaluation Dataset.

`prompt_hash` is SHA-256 over canonicalized prompt ID, prompt version, Schema
version, system template, output Schema, and examples. It is a reproducibility
and integrity fingerprint, not a digital signature.

Any semantic prompt, Schema, or example change requires a new immutable semantic
version and contract tests. Version names such as `latest`, `current`, or `final`
are prohibited. Existing versions remain available while Replay evidence refers
to them.

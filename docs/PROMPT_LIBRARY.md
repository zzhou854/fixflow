# Prompt Library

Prompts are immutable-by-version, UTF-8, Git-managed assets under
`backend/app/llm/prompts/`. They are not stored in PostgreSQL, downloaded at
runtime, injected through environment variables, or editable through an API or
frontend.

The Registry supports two explicit versions:

| Version | System | Examples | Schema | Status |
|---|---|---|---|---|
| `1.0.0` | `system_v1.md` | `examples_v1.json` (16) | `output_schema_v1.json` | frozen runtime default |
| `2.0.0` | `system_v2.md` | `examples_v2.json` (20) | shared `output_schema_v1.json` | development `NOT_READY` |

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

Task 14 freezes version `1.0.0` and its Hash while evaluating it. The synthetic
Golden Dataset is a separate Git asset and may not copy Few-shot inputs exactly.
Discovering a quality weakness creates a future Prompt Candidate; neither the
Prompt nor Golden labels are silently changed to make a run pass.

Task 16 adds explicit Registry selection without changing the default.
`resident_interpretation@2.0.0` has Hash
`ec67bc01ac5beec2dec99bf9af18004f496045f8ed0f0e0c2811a070c2682c2c`.
It uses the same formal output Schema, stricter intent/clarification/safety and
tool boundaries, and compact example serialization. Its assembled system
message is 8,745 characters versus 9,494 for v1. Evaluation may select v2
through internal CLI/DI; Resident requests cannot choose a Prompt version.
The online development run returned `NOT_READY`, so v2 is neither the runtime
default nor a Release Candidate.

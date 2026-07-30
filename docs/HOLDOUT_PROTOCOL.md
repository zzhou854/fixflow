# Future locked Holdout protocol

Phase 1C prepares offline tooling and governance only. It creates no new
Holdout corpus, reveals no future cases to prompt development, and performs no
Holdout live call.

## Lifecycle

```text
DRAFT -> SEALED -> CONSUMED
   \         \          \
    +---------+-----------> INVALIDATED
```

- `DRAFT`: authoring is allowed and live use is forbidden.
- `SEALED`: content hash, case count, scorer, gate, Prompt, and schema are
  frozen; only this state may begin a formal qualification.
- `CONSUMED`: the first live call atomically records its timestamp and changes
  the state. A consumed corpus cannot be reused as a new blind Holdout.
- `INVALIDATED`: content or identity changed after manifest creation, so the
  manifest cannot authorize qualification.

The manifest contains:

```text
dataset_id
dataset_version
case_count
dataset_sha256
scorer_version
gate_version
prompt_version
schema_version
created_at
locked_at
first_live_call_at
live_call_count
status
```

Before sealing, `scan_duplicates()` checks Case IDs, exact text, normalized
text, and supplied known fingerprints against development and historical
corpora. The corpus file and every evaluation identity are reverified before
the first call. Any content, scorer, gate, Prompt, or schema change invalidates
the manifest.

Qualification audit records are append-only JSONL. Results must use a new
artifact path and must never overwrite a prior run. The tool records validated
structured evidence, hashes, versions, status, and safe error codes only. It
must not record credentials, headers, raw HTTP, full Prompt text, complete raw
provider responses, private reasoning, or business data.

## First-call rule

The caller must persist the transition to `CONSUMED` before dispatching the
first online request. If that transition cannot be durably audited, the call is
forbidden. This phase implements and tests the transition functions but does
not invoke them against a real future corpus.

## Separation from development

The current 120-case `resident_interpretation@1.0.0` corpus is explicitly
`DEVELOPMENT`. Historical Challenge versions and the architecture-3 Holdout
are consumed research evidence. None may be relabeled as a new blind Holdout.

The next separately approved phase must author an unseen corpus, seal it before
any model call, review the fixed scorer/gate identities, and run it once. Phase
1C does not set `HOLDOUT_PASSED`.

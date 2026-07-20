# Policy RAG

## Scope and data declaration

Policy corpus is synthetic demonstration data for FixFlow. It is maintained in
`data/policies/fixflow_demo_policies.json`; it is not legal advice, a government
rule, or the official policy of a property-management company. The strict JSON
schema rejects undeclared fields and executable configuration.

Task 7 implements a provider-neutral, read-only policy-evidence boundary:

```text
future Agent node
  -> PolicyRetrievalService
  -> PolicyRepository
  -> PostgreSQL 16 + pgvector
```

It cannot create or mutate tickets/appointments, authorize users, modify domain
status, set severity, advance workflow, or call MCP. LangGraph, the MCP Client,
and a real online Embedding Provider remain deferred.

## Storage

Revision `20260720_0003` adds:

- `policy_documents`: code/version, title, optional issue category, finite
  topic, authority rank, effective interval, synthetic source identifier,
  SHA-256 content hash, enabled flag, timestamps, and exact embedding profile;
- `policy_chunks`: document/index, bounded content, `vector(384)`, explicit
  search terms, optional paired decision key/value, and timestamp.

`(policy_code, version)` and `(document_id, chunk_index)` are unique. A named
GiST exclusion constraint prevents overlapping effective periods for the same
policy code. Foreign-key deletion is `RESTRICT`; disabling or expiration is
preferred to physical deletion. The shared `vector` and `btree_gist`
extensions are ensured during upgrade and preserved during downgrade.

Effective time is the half-open interval `[effective_from, effective_to)`:

```text
is_enabled
and effective_from <= as_of
and (effective_to is null or as_of < effective_to)
```

`as_of` must be timezone-aware. Enabled, category, topic, and effective-time
filters execute in SQL before any candidate is returned to Python. Category
matching permits the requested category or a general document whose category
is null; another category's specialist policy cannot enter the candidate set.

## Embeddings and import

`POLICY_EMBEDDING_DIMENSION = 384` is centralized for ORM, provider contracts,
and tests; the immutable migration records the same literal. A future online
provider must produce exactly this dimension or require a separately reviewed
schema/version migration. The small release-1 corpus uses exact pgvector cosine
distance and intentionally has no HNSW or IVFFlat index.

`EmbeddingProvider` is vendor-neutral and exposes an immutable
`EmbeddingProfile(provider, model, dimension, profile_version)` without
credentials. Every returned vector must carry that profile. Import persists it;
retrieval reads eligible corpus profiles without vector comparison, requires an
exact match, and only then executes cosine retrieval. Equal dimensions alone
are not compatible. Switching providers requires explicit re-import/rebuild;
automatic re-embedding remains deferred.
The test provider hashes normalized character bigrams into deterministic
vectors; it demonstrates pipeline behavior, not real semantic-model quality.

Import computes a canonical SHA-256 hash, embeds all chunks as one bounded
batch, validates every dimension, and writes the document and chunks in one
Policy Unit of Work. Replaying the same code/version/hash returns the existing
document only when its profile also matches. A different profile raises
`EmbeddingProfileConflict`; different content is a conflict. Provider
or chunk failure cannot leave a partial document. Policy seed rows are not
hard-coded in the Migration.

Document and chunk IDs are deterministic UUIDv5 values derived from policy
code, version, canonical content hash, and (for chunks) index. Two fresh
databases importing the same corpus produce identical evidence IDs. Content or
ordering changes cannot silently reuse an old ID for different evidence.

## Hybrid retrieval

The retrieval order is fixed:

1. SQL enabled/category/topic/effective-time filtering;
2. cosine vector retrieval (`vector_similarity = 1 - cosine_distance`, not a
   probability); candidates below `minimum_vector_similarity` do not
   enter that lane;
3. deterministic lexical matching against normalized explicit `search_terms`;
4. normalized Reciprocal Rank Fusion;
5. stable ordering, conflict detection, and sufficiency evaluation.

The lexical lane uses NFKC normalization, case folding, whitespace collapsing,
and explicit substring terms. It does not use an English tokenizer, hidden
synonyms, LLM-generated query expansion, or per-chunk database queries.

RRF uses `RRF_K = 60`:

```text
raw = 1 / (60 + vector_rank) + 1 / (60 + lexical_rank)
fusion_score = raw / (2 / 61)
```

Only lanes containing a candidate contribute. Equal lane scores use dense
ranks. Final order is fusion score descending, authority rank descending,
effective-from descending, then stable chunk UUID. `fusion_score` is a
normalized RRF ranking score, not semantic similarity or a probability.
`minimum_vector_similarity` and `minimum_fusion_score` belong to different score
spaces and are never interchanged. Evidence retains vector similarity/rank,
lexical score/rank, and fusion score for audit.

## Evidence, conflicts, and sufficiency

`PolicyEvidence` returns a stable chunk-based evidence ID, document/code/version,
topic/category, bounded excerpt, synthetic source, effective interval,
authority, vector/lexical ranks, fusion score, decision pair, retrieval time,
and an instruction-like-content flag. It never returns the embedding, ORM
object, SQL, exception stack, or connection string.

Conflicts are deterministic: among effective returned evidence, the same
normalized non-empty decision key with two or more different normalized values
is a conflict. Duplicate equal values are not. All conflicting evidence IDs are
retained, and a higher authority rank does not silently remove lower-rank
conflict evidence.

Sufficiency is conservative:

- `CONFLICTING` when any conflict exists;
- `INSUFFICIENT` when evidence is absent or any requested topic lacks evidence
  passing an approved channel and `minimum_fusion_score`;
- `SUFFICIENT` only when every requested topic is covered and no conflict exists.

An approved channel is either vector similarity meeting
`minimum_vector_similarity` or a positive explicit-term lexical score. Thus a
lexical-only exact hit or vector-only high-similarity hit can be sufficient
(single-channel rank one has fusion score about `0.5`); low-quality evidence in
both channels cannot. Topic coverage and absence of conflict remain mandatory.

The result reports missing topics, `retrieved_as_of`, intent version, category,
requested topics, embedding profile, and a stable query fingerprint. The
fingerprint covers normalized query, sorted topics, category, `as_of`, `top_k`,
both thresholds, profile, and intent version; it excludes `trace_id`.
`merge_policy_result` recomputes it against current State and the expected
request. Any stale intent/category/topics/fingerprint raises
`StalePolicyResult` without mutating State. It does not change workflow state. `merge_policy_result`
may update only policy evidence metadata in Agent State; a later deterministic
orchestrator decides whether to ask a question or route to human review.

## Untrusted content boundary

Retrieved policy content is untrusted evidence data. It cannot override system
instructions, tool permissions, request filters, or business rules. A small
deterministic scanner flags phrases such as “ignore system” or “直接关闭工单”,
but this is only a signal and does not claim to eliminate Prompt Injection. A
future Prompt Builder must quote evidence in an explicit data boundary, and
real-provider grounding still requires Trace and evaluation.

## Frozen evaluation

The 21-case set in `backend/tests/fixtures/policy_retrieval_cases.json` is a
small pipeline evaluation, not a final product test set. It covers
all three issue categories, general process topics, effective-time boundaries,
expired/future/disabled exclusion, explicit conflicts, no evidence, multi-topic
coverage, and malicious text. Metrics are Recall@K, MRR, forbidden-policy
retrieval rate, expired-policy retrieval rate, conflict accuracy, and
sufficiency accuracy. Expected results are manually frozen; no LLM judge is
used. The deterministic character-bigram provider and explicit search terms
both participate. These metrics do not prove real Chinese semantic retrieval
quality and must not be described as 100% production accuracy. The same cases
must be rerun for an online provider; Stage D requires a larger independently
frozen set.

## Effective-period integrity

`ex_policy_documents_code_effective_overlap` applies `policy_code WITH =` and
`tstzrange(effective_from, COALESCE(effective_to, 'infinity'), '[)') WITH &&`.
Adjacent periods are allowed; open-ended or concurrent overlap is rejected with
SQLSTATE `23P01`. The Policy Unit of Work maps this to
`PolicyEffectivePeriodConflict`, and the failed transaction rolls back fully.
Disabled documents remain constrained by design: disablement controls retrieval
visibility, not version-history integrity.

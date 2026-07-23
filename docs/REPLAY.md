# Deterministic Replay

## Definition and boundary

FixFlow replay is a read-only verification of a recorded control-plane run. It
re-executes the committed Single Orchestrator topology against a strict tape of
already validated interpretation, policy, MCP read, and mutation-delivery
results. It compares the graph path, router decisions, interrupt, mutation
plan, and final replay-safe state.

Replay is not Retry, Resume, reconciliation, Event Sourcing, database time
travel, or restoration of historical business data. It never calls the live
LLM, embedding provider, policy search, MCP server, business Repository, or
formal PostgreSQL Checkpointer. It never resends a mutation. A replay execution
is new diagnostic evidence and cannot become a business fact.

## Persistent artifacts

Migration `20260723_0006` adds:

- `agent_replay_bundles`: one capture per original `agent_runs` row;
- `agent_replay_steps`: a gap-free, ordered, typed tape;
- `agent_replay_executions`: idempotent Operator verification attempts.

A Bundle moves from `CAPTURING` to `READY`, `INCOMPLETE`, or `INVALID`.
Historical runs that predate capture are reported as `UNAVAILABLE`; the system
does not synthesize a false READY bundle. READY requires a start projection,
expected projection, route/state fingerprints, finalization timestamp, and
bundle checksum. Step and Bundle checksums use canonical JSON. Schema version,
Graph schema version, and runtime revision are explicit compatibility evidence.

The runtime enforces bounded step and payload sizes. Capture failure never
rolls back or retries the business operation: the Bundle becomes INCOMPLETE and
safe runless Trace evidence records `replay_bundle_incomplete` and
`replay_capture_failed`.

## Replay-safe state and privacy

`ReplaySafeAgentState` contains typed identity linkage, authorized property,
intent/version, workflow stage, normalized issue facts, policy evidence IDs,
candidate summaries, authoritative snapshot versions, pending-operation
projection, and reconciliation projection. It does not contain raw JWTs,
passwords, API keys, database URLs, SQL, checkpoint blobs, prompts, Chain of
Thought, raw provider/MCP payloads, or full conversation text.

Conversation entries are reduced to role, message ID, content SHA-256, length,
and language. The structured issue summary is the validated Agent fact used by
the formal graph, not a stored transcript. Pending idempotency material is
replaced by a stable, non-secret projection. Snapshot `observed_at` telemetry,
Trace ID, run status, and conversation metadata are excluded from the control
fingerprint; versioned domain facts remain included.

## Capture and tape

The Graph wrapper emits node entry and route decisions through
`ReplayCapturePort`. Focused recording adapters capture validated results at
the interpretation, policy, and property-operations boundaries. Node code does
not access the Replay Repository.

The tape has strict sequence numbers, unique step keys, typed payload schemas,
request fingerprints, and per-step checksums. The Replay Engine validates the
complete artifact before execution. Missing calls, unexpected calls, wrong
order, request mismatches, unconsumed steps, schema mismatch, and checksum
failure produce closed mismatch/status values.

Mutation tape entries contain the result or `UNKNOWN_COMMIT` delivery
classification. `RecordedPropertyOperationsClient` returns that recorded
outcome only. `MutationGuard` fails closed if replay attempts an unsupported
live mutation.

## Execution and comparison

`ReplayEngine` builds the same formal graph with an in-memory saver scoped to
the verification. Resume is primed in that disposable saver and then receives
the strict recorded Resume union. Existing-thread property preflight is
consumed from tape. No formal Checkpoint table is read or written.

Statuses are:

- `PASSED`: all compared control evidence matches;
- `DIVERGED`: replay completed but one or more comparisons differ;
- `INCOMPLETE`: a required tape step is missing or unexpected;
- `UNSUPPORTED_SCHEMA`: the artifact cannot be interpreted safely;
- `FAILED_SAFE`: replay infrastructure stopped safely.

Ten executions of the same Bundle, and an execution after rebuilding the
engine, must produce the same route/state fingerprints. Replay uses the
Bundle's reference time; current database state is never injected into the
historical verification. The console may separately query current business
facts for comparison.

## Trace and recommendations

Replay uses the closed `REPLAY` Trace source. Bundle capture emits
`replay_bundle_started`, `replay_bundle_ready` or
`replay_bundle_incomplete`. Verification emits `replay_requested`,
`replay_started`, and exactly one final replay status event. These are runless
diagnostic events; they are never appended to an already terminal original
Run. The final execution row and final Trace event commit in one transaction.

Recovery recommendations are deterministic and read-only:
`NO_ACTION_REQUIRED`, `OWNER_RESUME_REQUIRED`,
`WAIT_FOR_RECONCILIATION`, `OPERATOR_RECHECK_ALLOWED`,
`MANUAL_REVIEW_REQUIRED`, `REPLAY_EVIDENCE_INCOMPLETE`, and
`REPLAY_DIVERGED_REVIEW_REQUIRED`. A recommendation never applies a Replay
result, modifies a Checkpoint, resends a mutation, or changes business state.

# Typed Agent core

## Scope

Task 6 implements the provider-neutral language core for the future single
orchestrator. It does not implement LangGraph, checkpoint persistence, an MCP
client, policy retrieval, an online model, or a business-state engine.
PostgreSQL remains the source of truth for tickets and appointments.

```text
future single orchestrator
  -> typed Agent State and deterministic merge/router
  -> interpret_message / compose_response
  -> LLMProvider Protocol
```

Language nodes do not import Application services, MCP, repositories, ORM, or
Unit of Work. Model output cannot authorize a property, set a business status,
set severity, advance a workflow stage, claim tool success, or replace State.

## State ownership

`AgentState` is a closed, strict, JSON-serializable Pydantic model. Its main
field groups are:

```text
identity: thread_id, trace_id, actor_type, actor_id, user_id, property_id
references: active_ticket_id, active_appointment_id
intent: task_intent, utterance_intent, intent_version
issue: issue_category, issue_location, normalized_issue_location,
       issue_description, severity
risk: safety_flags, safety_review_required
time: user_availability_windows, candidate_slots
planning: missing_fields, policy_evidence_ids, policy_conflict,
          policy_sufficiency, missing_policy_topics, policy_retrieved_as_of,
          policy_query_fingerprint, workflow_stage, pending_action,
          user_confirmation
snapshots: ticket_snapshot_version, appointment_version,
           snapshot_refresh_required
execution: last_tool_result, retry_count, escalation_reason
```

`user_availability_windows` are the resident's stated constraints.
`candidate_slots` are deterministic query results containing worker ID, start,
end, rank, and `booking_guaranteed=false`. They are not appointments. A changed
task goal or issue identity clears both collections; new risk evidence clears
candidate slots but retains the resident's stated availability.

Identity, authorization, active resource IDs, and snapshot versions cannot be
written by `InterpretMessageOutput` or replaced by merge. An explicit property
reference is only a claim to compare against the already authorized
`property_id`; it never becomes authorization. When a new task identity makes a
snapshot potentially stale, merge preserves the versions and sets
`snapshot_refresh_required=true` so deterministic orchestration can reread
PostgreSQL.

## Utterance intent and task intent

`utterance_intent` describes only the latest message. `task_intent` is the
durable multi-turn goal. Transient utterances such as `PROVIDE_INFORMATION`,
slot selection, and acceptance/rejection do not replace that goal.

`intent_version` increments only when an established durable task goal changes,
or when an established issue category or normalized issue location changes.
Supplying a missing field, repeating the same goal, equivalent formatting, or
adding an ordinary description does not increment it.

| Change | Increment | Deterministic invalidation |
| --- | --- | --- |
| Established category or normalized location changes | Yes | policy evidence/conflict, user availability, candidate slots, confirmation, pending action, tool result; request snapshot refresh |
| Established task goal changes | Yes | same as above |
| Initial missing field supplied | No | recompute missing fields only |
| Transient or repeated utterance | No | none |
| New safety evidence | No unless another versioned change occurs | policy evidence/conflict, candidate slots, confirmation, pending action, tool result; require safety review |

## Deterministic missing fields

The model may return `model_suggested_missing_fields`, but this advisory value
never overwrites State. Merge always recomputes `missing_fields` from typed
state using one deterministic matrix:

- `NEW_REPAIR`: authorized property, issue category, normalized issue location,
  and issue description are required.
- `RESCHEDULE_APPOINTMENT`: at least one `user_availability_window` is required.
- Other current task goals have no Task-6 field requirements.

The same computation is enforced by State validation, so an inconsistent State
cannot be constructed through normal Pydantic validation.

## Risk boundary

Safety flags are model-extracted evidence, not a domain decision. New flags set
`safety_review_required=true` and invalidate risk-dependent plans. Merge does
not set `severity` and does not change `workflow_stage`. A separate,
deterministic router may observe the typed flag and set the frozen
`EMERGENCY_REVIEW` workflow stage. Later policy and domain logic still decide
business severity and escalation; the model cannot do so.

## Time interpretation

`InterpretMessageInput` requires a timezone-aware `reference_time` and a valid
IANA `timezone_name`. The reference offset must match that named timezone at the
given instant. The prompt instructs the provider to resolve relative expressions
such as “tomorrow morning” only from this context and to return aware absolute
timestamps. The node rejects returned availability windows whose offsets do not
match the named timezone. It never uses server local time as an implicit clock.

## Language nodes and evidence boundary

`interpret_message:v1` receives bounded recent messages, a small state summary,
known issue fields, deterministic missing fields, current workflow stage, and
explicit time context. Its closed output can express only the latest utterance
intent, issue facts, safety evidence, user availability, correction,
acceptance, requested human help, an explicit property claim, and advisory
missing-field suggestions. Unknown enums, naive/reversed intervals, wrong time
offsets, and undeclared identity/status/workflow fields are rejected.

`compose_response:v1` receives an allowlist of verified business facts with
stable `fact_id` values, allowed policy evidence with stable `evidence_id`
values, workflow stage, required user action, and sanitized error information.
This input boundary and prompt reduce unsupported claims; they do not prove that
a real probabilistic model can never hallucinate. Runtime citation validation,
requiring the model to return referenced fact/evidence IDs, and evaluation of
unsupported-claim rates are explicitly deferred to the reliability/evaluation
stage.

Prompt name and version are centralized and returned in node metadata for later
Trace, replay, and evaluation.

## Provider and failures

`LLMProvider` exposes structured generation, text generation, and health checks
without importing a vendor SDK. Results contain provider/model/prompt metadata
but no credentials. The test-only `ScriptedLLMProvider` records calls and can
inject invalid output, timeout, or unavailability without keyword heuristics.

Errors are transport-neutral: `AgentError`, `LLMProviderUnavailable`,
`LLMTimeout`, `StructuredOutputInvalid`, `PromptInputInvalid`, and
`StateMergeConflict`. Exception chains are preserved. The nodes have no State or
database write handle, so invalid output and provider failures cannot partially
mutate State or business data.

Online provider integration, MCP client, LangGraph/checkpoints, conversation
persistence, JWT/API, Trace runtime, frontend, Harness, and system-level
evaluation remain deferred mandatory work.

Task 7 now provides the policy retrieval service, effective-time filtering,
conflict/sufficiency result, and frozen retrieval evaluation. The LangGraph
integration remains deferred. `merge_policy_result` can update only policy
evidence IDs, conflict/sufficiency, missing topics, retrieval time, and query
fingerprint. It cannot modify identity, resource IDs, snapshots, severity,
workflow stage, pending action, or confirmation. Task-6 intent invalidation also
clears all of these policy metadata fields.
Merge additionally requires the result intent version and deterministic query
fingerprint to match current State and the expected request. A late result is
rejected without clearing or overwriting newer policy metadata.

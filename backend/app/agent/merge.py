"""Deterministic interpretation merge and stale-plan invalidation."""

from app.agent.enums import AgentIntent, PendingAction
from app.agent.errors import StateMergeConflict
from app.agent.models import InterpretMessageOutput, normalize_issue_location
from app.agent.state import AgentState, compute_missing_fields
from app.domain.enums import WorkflowStage

TASK_INTENTS: frozenset[AgentIntent] = frozenset(
    {
        AgentIntent.NEW_REPAIR,
        AgentIntent.QUERY_TICKET_STATUS,
        AgentIntent.RESCHEDULE_APPOINTMENT,
        AgentIntent.CANCEL_APPOINTMENT,
        AgentIntent.CANCEL_TICKET,
        AgentIntent.REQUEST_HUMAN,
    }
)


def resolve_task_intent(current: AgentIntent, utterance: AgentIntent) -> AgentIntent:
    """Map a current utterance to the durable task goal without model discretion."""

    if utterance in TASK_INTENTS:
        return utterance
    return current


def route_required_safety_review(state: AgentState) -> AgentState:
    """Deterministically route an already flagged state to the frozen review stage."""

    if not state.safety_review_required:
        return state
    return state.model_copy(update={"workflow_stage": WorkflowStage.EMERGENCY_REVIEW})


def merge_interpretation(state: AgentState, output: InterpretMessageOutput) -> AgentState:
    """Merge validated extraction without allowing the model to replace state."""

    new_location = (
        output.issue_location if output.issue_location is not None else state.issue_location
    )
    normalized_location = normalize_issue_location(new_location)
    category_changed = (
        output.issue_category is not None
        and state.issue_category is not None
        and output.issue_category is not state.issue_category
    )
    location_changed = (
        normalized_location is not None
        and state.normalized_issue_location is not None
        and normalized_location != state.normalized_issue_location
    )
    merged_task_intent = resolve_task_intent(state.task_intent, output.utterance_intent)
    intent_changed = (
        state.task_intent is not AgentIntent.UNKNOWN and merged_task_intent is not state.task_intent
    )
    version_changed = category_changed or location_changed or intent_changed
    new_safety = tuple(flag for flag in output.safety_flags if flag not in state.safety_flags)
    invalidate_plan = version_changed or bool(new_safety)

    merged_category = output.issue_category or state.issue_category
    merged_description = output.issue_description_update or state.issue_description
    merged_user_availability = output.user_availability_windows or state.user_availability_windows
    updates: dict[str, object] = {
        "task_intent": merged_task_intent,
        "utterance_intent": output.utterance_intent,
        "issue_category": merged_category,
        "issue_location": new_location,
        "normalized_issue_location": normalized_location,
        "issue_description": merged_description,
        "safety_flags": tuple(dict.fromkeys((*state.safety_flags, *output.safety_flags))),
        "user_availability_windows": merged_user_availability,
        "intent_version": state.intent_version + int(version_changed),
    }
    if output.explicit_property_reference is not None:
        if output.explicit_property_reference != state.property_id:
            raise StateMergeConflict("LLM extraction cannot replace an authorized property")
    if invalidate_plan:
        updates.update(
            policy_evidence_ids=(),
            policy_conflict=False,
            policy_sufficiency=None,
            missing_policy_topics=(),
            policy_retrieved_as_of=None,
            policy_query_fingerprint=None,
            candidate_slots=(),
            candidate_slots_fingerprint=None,
            selected_candidate_slot=None,
            duplicate_ticket_candidates=(),
            duplicate_candidates_fingerprint=None,
            user_confirmation=None,
            pending_action=PendingAction.NONE,
            pending_operation=None,
            last_tool_result=None,
        )
    if version_changed:
        updates.update(
            user_availability_windows=(),
            service_duration_minutes=None,
            snapshot_refresh_required=True,
        )
        merged_user_availability = ()
    if new_safety:
        updates["safety_review_required"] = True
    updates["missing_fields"] = compute_missing_fields(
        task_intent=merged_task_intent,
        property_id=state.property_id,
        issue_category=merged_category,
        normalized_issue_location=normalized_location,
        issue_description=merged_description,
        user_availability_windows=merged_user_availability,
    )
    return state.model_copy(update=updates)

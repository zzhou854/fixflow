"""Deterministic task intent, missing fields, risk, and identity merge tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.agent.enums import AgentIntent, IssueField, PendingAction, SafetyFlag
from app.agent.errors import StateMergeConflict
from app.agent.merge import merge_interpretation, route_required_safety_review
from app.agent.models import InterpretMessageOutput, TimeWindow
from app.agent.state import AgentState
from app.domain.enums import ActorType, IssueCategory, Severity, WorkflowStage


def _output(**changes: object) -> InterpretMessageOutput:
    payload: dict[str, object] = {"utterance_intent": AgentIntent.NEW_REPAIR}
    payload.update(changes)
    return InterpretMessageOutput.model_validate(payload)


def test_category_change_increments_version_and_invalidates_plan(agent_state: AgentState) -> None:
    merged = merge_interpretation(agent_state, _output(issue_category=IssueCategory.ELECTRICAL))
    assert merged.intent_version == 4
    assert merged.policy_evidence_ids == ()
    assert merged.user_availability_windows == ()
    assert merged.candidate_slots == ()
    assert merged.pending_action is PendingAction.NONE
    assert merged.user_confirmation is None
    assert merged.last_tool_result is None
    assert merged.snapshot_refresh_required is True
    assert merged.ticket_snapshot_version == agent_state.ticket_snapshot_version
    assert merged.appointment_version == agent_state.appointment_version


def test_location_change_increments_version(agent_state: AgentState) -> None:
    merged = merge_interpretation(agent_state, _output(issue_location="Bathroom"))
    assert merged.intent_version == 4
    assert merged.normalized_issue_location == "bathroom"


def test_long_term_target_change_increments_version(agent_state: AgentState) -> None:
    merged = merge_interpretation(
        agent_state, _output(utterance_intent=AgentIntent.QUERY_TICKET_STATUS)
    )
    assert merged.task_intent is AgentIntent.QUERY_TICKET_STATUS
    assert merged.utterance_intent is AgentIntent.QUERY_TICKET_STATUS
    assert merged.intent_version == 4


def test_new_repair_to_provide_information_preserves_task_and_version(
    agent_state: AgentState,
) -> None:
    merged = merge_interpretation(
        agent_state,
        _output(
            utterance_intent=AgentIntent.PROVIDE_INFORMATION,
            issue_description_update="More detail",
        ),
    )
    assert merged.task_intent is AgentIntent.NEW_REPAIR
    assert merged.utterance_intent is AgentIntent.PROVIDE_INFORMATION
    assert merged.intent_version == 3


def test_reschedule_to_provide_information_keeps_reschedule_goal(
    agent_state: AgentState,
) -> None:
    rescheduling = agent_state.model_copy(
        update={
            "task_intent": AgentIntent.RESCHEDULE_APPOINTMENT,
            "utterance_intent": AgentIntent.RESCHEDULE_APPOINTMENT,
            "missing_fields": (),
        }
    )
    merged = merge_interpretation(
        rescheduling,
        _output(utterance_intent=AgentIntent.PROVIDE_INFORMATION),
    )
    assert merged.task_intent is AgentIntent.RESCHEDULE_APPOINTMENT
    assert merged.intent_version == 3


def test_select_slot_is_utterance_action_without_losing_task_context(
    agent_state: AgentState,
) -> None:
    merged = merge_interpretation(
        agent_state,
        _output(utterance_intent=AgentIntent.SELECT_APPOINTMENT_SLOT),
    )
    assert merged.task_intent is AgentIntent.NEW_REPAIR
    assert merged.utterance_intent is AgentIntent.SELECT_APPOINTMENT_SLOT
    assert merged.intent_version == 3


def test_repeated_new_repair_does_not_increment(agent_state: AgentState) -> None:
    merged = merge_interpretation(agent_state, _output())
    assert merged.task_intent is AgentIntent.NEW_REPAIR
    assert merged.intent_version == 3


def test_format_equivalent_location_does_not_increment(agent_state: AgentState) -> None:
    merged = merge_interpretation(agent_state, _output(issue_location="  KITCHEN  "))
    assert merged.intent_version == 3
    assert merged.policy_evidence_ids == agent_state.policy_evidence_ids


def test_description_supplement_does_not_increment(agent_state: AgentState) -> None:
    merged = merge_interpretation(
        agent_state, _output(issue_description_update="Pipe leak below the sink")
    )
    assert merged.intent_version == 3
    assert merged.issue_description == "Pipe leak below the sink"


def test_new_safety_signal_only_requires_review_and_invalidates_risk_plan(
    agent_state: AgentState,
) -> None:
    merged = merge_interpretation(
        agent_state, _output(safety_flags=(SafetyFlag.ELECTRICAL_HAZARD,))
    )
    assert merged.intent_version == 3
    assert merged.safety_review_required is True
    assert merged.workflow_stage is agent_state.workflow_stage
    assert merged.severity is Severity.MEDIUM
    assert merged.policy_evidence_ids == ()
    assert merged.candidate_slots == ()
    assert merged.user_availability_windows == agent_state.user_availability_windows
    assert merged.pending_action is PendingAction.NONE


def test_only_deterministic_router_enters_frozen_safety_review_stage(
    agent_state: AgentState,
) -> None:
    merged = merge_interpretation(agent_state, _output(safety_flags=(SafetyFlag.ACTIVE_FLOODING,)))
    routed = route_required_safety_review(merged)
    assert merged.workflow_stage is WorkflowStage.AWAITING_SLOT_CONFIRMATION
    assert routed.workflow_stage is WorkflowStage.EMERGENCY_REVIEW
    assert routed.severity is Severity.MEDIUM


def test_model_empty_missing_list_cannot_hide_missing_category(agent_state: AgentState) -> None:
    incomplete = agent_state.model_copy(
        update={
            "issue_category": None,
            "missing_fields": (IssueField.ISSUE_CATEGORY,),
        }
    )
    merged = merge_interpretation(
        incomplete,
        _output(model_suggested_missing_fields=()),
    )
    assert merged.missing_fields == (IssueField.ISSUE_CATEGORY,)


def test_model_false_missing_suggestion_cannot_remove_valid_location(
    agent_state: AgentState,
) -> None:
    merged = merge_interpretation(
        agent_state,
        _output(model_suggested_missing_fields=(IssueField.ISSUE_LOCATION,)),
    )
    assert merged.issue_location == "Kitchen"
    assert IssueField.ISSUE_LOCATION not in merged.missing_fields


def test_supplying_one_field_removes_only_that_missing_item(agent_state: AgentState) -> None:
    incomplete = agent_state.model_copy(
        update={
            "issue_category": None,
            "issue_location": None,
            "normalized_issue_location": None,
            "missing_fields": (IssueField.ISSUE_CATEGORY, IssueField.ISSUE_LOCATION),
        }
    )
    merged = merge_interpretation(
        incomplete,
        _output(issue_category=IssueCategory.WATER_LEAK),
    )
    assert merged.missing_fields == (IssueField.ISSUE_LOCATION,)


def test_user_correction_recomputes_missing_fields(agent_state: AgentState) -> None:
    merged = merge_interpretation(
        agent_state,
        _output(
            issue_location="Bathroom", model_suggested_missing_fields=(IssueField.ISSUE_LOCATION,)
        ),
    )
    assert merged.normalized_issue_location == "bathroom"
    assert merged.missing_fields == ()


def test_all_identity_authorization_and_snapshot_fields_remain_unchanged(
    agent_state: AgentState,
) -> None:
    merged = merge_interpretation(
        agent_state,
        _output(utterance_intent=AgentIntent.QUERY_TICKET_STATUS),
    )
    protected = (
        "actor_type",
        "actor_id",
        "user_id",
        "property_id",
        "active_ticket_id",
        "active_appointment_id",
        "ticket_snapshot_version",
        "appointment_version",
    )
    for field in protected:
        assert getattr(merged, field) == getattr(agent_state, field)
    assert merged.actor_type is ActorType.RESIDENT


def test_llm_property_reference_cannot_replace_authorized_property(
    agent_state: AgentState,
) -> None:
    with pytest.raises(StateMergeConflict):
        merge_interpretation(agent_state, _output(explicit_property_reference=uuid4()))


def test_unverified_property_reference_cannot_become_authorized_state(
    agent_state: AgentState,
) -> None:
    without_property = agent_state.model_copy(update={"property_id": None})
    with pytest.raises(StateMergeConflict):
        merge_interpretation(
            without_property,
            _output(explicit_property_reference=uuid4()),
        )


def test_user_availability_and_candidate_slots_have_distinct_lifecycles(
    agent_state: AgentState,
) -> None:
    start = datetime(2032, 1, 3, 9, tzinfo=UTC)
    window = TimeWindow(starts_at=start, ends_at=start + timedelta(hours=2))
    merged = merge_interpretation(agent_state, _output(user_availability_windows=(window,)))
    assert merged.user_availability_windows == (window,)
    assert merged.candidate_slots == agent_state.candidate_slots

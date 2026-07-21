from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.agent.enums import AgentIntent
from app.agent.models import InterpretMessageOutput
from app.agent.state import AgentState, CachedTicketSnapshot
from app.agent_runtime.models import AgentTurnInput
from app.agent_runtime.rules import (
    SERVICE_DURATION_MINUTES_BY_CATEGORY,
    apply_new_repair_defaults,
)
from app.domain.enums import ActorType, IssueCategory, Severity, TicketStatus
from pydantic import ValidationError


def _state(**updates: object) -> AgentState:
    state = AgentState(
        thread_id=uuid4(),
        trace_id=uuid4(),
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        user_id=uuid4(),
        property_id=uuid4(),
    )
    return state.model_copy(update=updates)


@pytest.mark.parametrize("category", tuple(IssueCategory))
def test_service_duration_is_centralized_and_always_sixty_minutes(
    category: IssueCategory,
) -> None:
    assert SERVICE_DURATION_MINUTES_BY_CATEGORY[category] == 60
    state = _state(task_intent=AgentIntent.QUERY_TICKET_STATUS, issue_category=category)
    assert apply_new_repair_defaults(state).service_duration_minutes == 60


def test_complete_ordinary_new_repair_defaults_to_medium() -> None:
    state = _state(
        task_intent=AgentIntent.NEW_REPAIR,
        issue_category=IssueCategory.WATER_LEAK,
        issue_location="厨房",
        normalized_issue_location="厨房",
        issue_description="水槽漏水",
    )
    result = apply_new_repair_defaults(state)
    assert result.severity is Severity.MEDIUM
    assert result.service_duration_minutes == 60


def test_database_snapshot_severity_wins() -> None:
    ticket_id = uuid4()
    state = _state(
        active_ticket_id=ticket_id,
        task_intent=AgentIntent.NEW_REPAIR,
        issue_category=IssueCategory.ELECTRICAL,
        issue_location="客厅",
        normalized_issue_location="客厅",
        issue_description="插座失灵",
        severity=Severity.MEDIUM,
        cached_ticket_snapshot=CachedTicketSnapshot(
            ticket_id=ticket_id,
            ticket_version=2,
            ticket_status=TicketStatus.IN_PROGRESS,
            severity=Severity.HIGH,
            rework_count=0,
            observed_at=datetime.now(UTC),
        ),
    )
    assert apply_new_repair_defaults(state).severity is Severity.HIGH


def test_safety_signal_does_not_force_emergency_severity() -> None:
    state = _state(
        task_intent=AgentIntent.NEW_REPAIR,
        issue_category=IssueCategory.ELECTRICAL,
        issue_location="客厅",
        normalized_issue_location="客厅",
        issue_description="插座冒烟",
        safety_review_required=True,
    )
    result = apply_new_repair_defaults(state)
    assert result.severity is None
    assert result.service_duration_minutes == 60


def test_llm_output_rejects_identity_severity_and_workflow_fields() -> None:
    for forbidden in ("property_id", "severity", "workflow_stage", "ticket_status"):
        with pytest.raises(ValidationError):
            InterpretMessageOutput.model_validate(
                {"utterance_intent": "NEW_REPAIR", forbidden: str(uuid4())}
            )


def test_turn_input_requires_closed_typed_fields() -> None:
    with pytest.raises(ValidationError):
        AgentTurnInput.model_validate(
            {
                "thread_id": str(uuid4()),
                "trace_id": str(uuid4()),
                "actor_type": "RESIDENT",
                "actor_id": str(uuid4()),
                "user_id": str(uuid4()),
                "property_id": str(uuid4()),
                "user_message": "漏水",
                "reference_time": "2026-07-20T10:00:00+08:00",
                "timezone_name": "Asia/Shanghai",
                "severity": "HIGH",
            }
        )

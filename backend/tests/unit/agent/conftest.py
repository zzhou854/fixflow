"""Typed Agent test fixtures."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.agent.enums import AgentIntent, PendingAction
from app.agent.models import TimeWindow
from app.agent.state import AgentState, CandidateSlot, ToolResultSummary
from app.domain.enums import ActorType, IssueCategory, Severity, WorkflowStage


@pytest.fixture
def agent_state() -> AgentState:
    start = datetime(2032, 1, 2, 9, tzinfo=UTC)
    return AgentState(
        thread_id=uuid4(),
        trace_id=uuid4(),
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        user_id=uuid4(),
        property_id=uuid4(),
        active_ticket_id=uuid4(),
        active_appointment_id=uuid4(),
        task_intent=AgentIntent.NEW_REPAIR,
        utterance_intent=AgentIntent.NEW_REPAIR,
        intent_version=3,
        issue_category=IssueCategory.WATER_LEAK,
        issue_location="Kitchen",
        normalized_issue_location="kitchen",
        issue_description="Pipe leak",
        severity=Severity.MEDIUM,
        user_availability_windows=(
            TimeWindow(starts_at=start, ends_at=start + timedelta(hours=1)),
        ),
        candidate_slots=(
            CandidateSlot(
                worker_id=uuid4(),
                scheduled_start=start,
                scheduled_end=start + timedelta(hours=1),
                rank=1,
            ),
        ),
        missing_fields=(),
        policy_evidence_ids=(uuid4(),),
        policy_conflict=True,
        workflow_stage=WorkflowStage.AWAITING_SLOT_CONFIRMATION,
        pending_action=PendingAction.BOOK_APPOINTMENT,
        user_confirmation=True,
        ticket_snapshot_version=2,
        appointment_version=1,
        last_tool_result=ToolResultSummary(
            result_code="FOUND", message="candidate slots", retryable=False
        ),
        retry_count=1,
    )

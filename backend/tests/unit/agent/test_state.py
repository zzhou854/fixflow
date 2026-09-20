"""Agent State schema and JSON-safety evidence."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.agent.models import TimeWindow
from app.agent.state import AgentState, CandidateSlot
from pydantic import ValidationError


def test_complete_state_constructs_and_serializes(agent_state: AgentState) -> None:
    payload = agent_state.model_dump(mode="json")
    assert payload["intent_version"] == 3
    assert payload["active_ticket_id"] == str(agent_state.active_ticket_id)


def test_state_json_round_trip_preserves_key_fields(agent_state: AgentState) -> None:
    restored = AgentState.model_validate_json(agent_state.model_dump_json())
    assert restored == agent_state


def test_illegal_enum_is_rejected(agent_state: AgentState) -> None:
    payload = agent_state.model_dump()
    payload["workflow_stage"] = "OLD_STAGE"
    with pytest.raises(ValidationError):
        AgentState.model_validate(payload)


def test_naive_availability_datetime_is_rejected() -> None:
    with pytest.raises(ValidationError):
        TimeWindow(
            starts_at=datetime(2032, 1, 1, 9),
            ends_at=datetime(2032, 1, 1, 10, tzinfo=UTC),
        )


def test_invalid_availability_range_is_rejected() -> None:
    start = datetime(2032, 1, 1, 9, tzinfo=UTC)
    with pytest.raises(ValidationError):
        TimeWindow(starts_at=start, ends_at=start - timedelta(minutes=1))


def test_candidate_slot_is_typed_and_never_claims_booking() -> None:
    start = datetime(2032, 1, 1, 9, tzinfo=UTC)
    slot = CandidateSlot(
        worker_id=uuid4(),
        scheduled_start=start,
        scheduled_end=start + timedelta(hours=1),
        rank=1,
    )
    assert slot.booking_guaranteed is False


def test_candidate_slot_rejects_booking_guarantee() -> None:
    start = datetime(2032, 1, 1, 9, tzinfo=UTC)
    with pytest.raises(ValidationError):
        CandidateSlot(
            worker_id=uuid4(),
            scheduled_start=start,
            scheduled_end=start + timedelta(hours=1),
            rank=1,
            booking_guaranteed=True,
        )


def test_arbitrary_object_and_extra_state_are_rejected(agent_state: AgentState) -> None:
    payload = agent_state.model_dump()
    payload["last_tool_result"] = object()
    payload["orm_entity"] = object()
    with pytest.raises(ValidationError):
        AgentState.model_validate(payload)


def test_inconsistent_normalized_location_is_rejected(agent_state: AgentState) -> None:
    payload = agent_state.model_dump()
    payload["normalized_issue_location"] = "bathroom"
    with pytest.raises(ValidationError):
        AgentState.model_validate(payload)

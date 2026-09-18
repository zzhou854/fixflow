"""The graph's whole routing surface stays deterministic and model-free."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.agent.enums import AgentIntent
from app.agent.models import TimeWindow
from app.agent.state import (
    AgentState,
    CachedAppointmentSnapshot,
    CachedTicketSnapshot,
    CandidateSlot,
    DuplicateTicketCandidate,
)
from app.agent_runtime.routing import (
    route_after_interpret,
    route_after_policy,
    route_after_slot_lookup,
    route_after_snapshot,
    route_duplicates,
)
from app.agent_runtime.runtime_state import dump_state
from app.domain.enums import (
    ActorType,
    AppointmentStatus,
    IssueCategory,
    Severity,
    TicketStatus,
)
from app.policy.enums import EvidenceSufficiency


def _new_repair_state(**updates: object) -> AgentState:
    intent = updates.pop("task_intent", AgentIntent.NEW_REPAIR)
    now = datetime.now(UTC)
    fields: dict[str, object] = {
        "thread_id": uuid4(),
        "trace_id": uuid4(),
        "actor_type": ActorType.RESIDENT,
        "actor_id": uuid4(),
        "user_id": uuid4(),
        "property_id": uuid4(),
        "property_context_verified": True,
        "task_intent": intent,
        "issue_category": IssueCategory.WATER_LEAK,
        "issue_location": "Kitchen sink",
        "normalized_issue_location": "kitchen sink",
        "issue_description": "Water leaks below the kitchen sink.",
        "severity": Severity.MEDIUM,
        "service_duration_minutes": 60,
    }
    if intent is AgentIntent.RESCHEDULE_APPOINTMENT:
        fields["user_availability_windows"] = (
            TimeWindow(starts_at=now, ends_at=now + timedelta(hours=2)),
        )
    fields.update(updates)
    return AgentState.model_validate(fields)


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"safety_review_required": True}, "finish_safety"),
        ({}, "retrieve_policy"),
        ({"task_intent": AgentIntent.QUERY_TICKET_STATUS}, "resolve_existing"),
        ({"task_intent": AgentIntent.RESCHEDULE_APPOINTMENT}, "resolve_existing"),
        ({"task_intent": AgentIntent.REQUEST_HUMAN}, "finish_manual_request"),
        ({"task_intent": AgentIntent.CANCEL_TICKET}, "finish_unsupported"),
        ({"task_intent": AgentIntent.ACCEPT_REPAIR}, "finish_unsupported"),
    ],
)
def test_interpret_router_covers_supported_and_deferred_intents(
    updates: dict[str, object], expected: str
) -> None:
    assert route_after_interpret(dump_state(_new_repair_state(**updates))) == expected


@pytest.mark.parametrize(
    ("conflict", "sufficiency", "expected"),
    [
        (False, EvidenceSufficiency.SUFFICIENT, "find_duplicates"),
        (True, EvidenceSufficiency.SUFFICIENT, "finish_policy_review"),
        (False, EvidenceSufficiency.INSUFFICIENT, "finish_policy_review"),
    ],
)
def test_policy_router_never_promotes_insufficient_or_conflicting_evidence(
    conflict: bool, sufficiency: EvidenceSufficiency, expected: str
) -> None:
    state = _new_repair_state(policy_conflict=conflict, policy_sufficiency=sufficiency)
    assert route_after_policy(dump_state(state)) == expected


def test_duplicate_router_distinguishes_none_one_and_many_candidates() -> None:
    no_candidates = _new_repair_state()
    one_candidate = _new_repair_state(active_ticket_id=uuid4())
    many_candidates = _new_repair_state(
        duplicate_ticket_candidates=(
            DuplicateTicketCandidate(
                ticket_id=uuid4(),
                ticket_version=1,
                ticket_status=TicketStatus.OPEN,
                issue_location="Kitchen sink",
            ),
        )
    )
    assert route_duplicates(dump_state(no_candidates)) == "prepare_create"
    assert route_duplicates(dump_state(one_candidate)) == "refresh_snapshot"
    assert route_duplicates(dump_state(many_candidates)) == "select_duplicate"


@pytest.mark.parametrize(
    ("intent", "selected", "snapshot", "expected"),
    [
        (AgentIntent.NEW_REPAIR, False, None, "list_slots"),
        (AgentIntent.QUERY_TICKET_STATUS, False, None, "compose"),
        (AgentIntent.NEW_REPAIR, True, None, "prepare_book"),
        (AgentIntent.RESCHEDULE_APPOINTMENT, True, None, "prepare_reschedule"),
        (AgentIntent.NEW_REPAIR, False, "active", "compose"),
    ],
)
def test_snapshot_router_covers_booking_rescheduling_status_and_human_paths(
    intent: AgentIntent,
    selected: bool,
    snapshot: str | None,
    expected: str,
) -> None:
    now = datetime.now(UTC)
    ticket_id, appointment_id, worker_id = uuid4(), uuid4(), uuid4()
    ends_at = now + timedelta(hours=1)
    candidate = CandidateSlot(
        worker_id=worker_id,
        scheduled_start=now,
        scheduled_end=ends_at,
        rank=1,
    )
    active = (
        CachedTicketSnapshot(
            ticket_id=ticket_id,
            ticket_version=1,
            ticket_status=TicketStatus.SCHEDULED,
            severity=Severity.MEDIUM,
            rework_count=0,
            active_appointment=CachedAppointmentSnapshot(
                appointment_id=appointment_id,
                worker_id=worker_id,
                appointment_status=AppointmentStatus.BOOKED,
                scheduled_start=now,
                scheduled_end=ends_at,
                appointment_version=1,
            ),
            observed_at=now,
        )
        if snapshot
        else None
    )
    state = _new_repair_state(
        task_intent=intent,
        active_ticket_id=ticket_id,
        cached_ticket_snapshot=active,
        selected_candidate_slot=candidate if selected else None,
        candidate_slots=(candidate,) if selected else (),
    )
    assert route_after_snapshot(dump_state(state)) == expected


def test_empty_slot_result_requests_another_time_instead_of_showing_empty_picker() -> None:
    state = _new_repair_state(
        user_availability_windows=(
            TimeWindow(
                starts_at=datetime(2030, 1, 10, 23, tzinfo=UTC),
                ends_at=datetime(2030, 1, 11, 1, tzinfo=UTC),
            ),
        ),
        candidate_slots=(),
    )

    assert route_after_slot_lookup(dump_state(state)) == "need_availability_information"

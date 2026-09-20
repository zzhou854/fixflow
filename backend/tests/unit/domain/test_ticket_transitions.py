"""Ticket transition-table tests."""

import pytest
from app.domain.enums import (
    AcceptanceRejectionReason,
    ActorType,
    CancellationReason,
    DomainFact,
    FailureReason,
    NoShowReason,
    TicketAction,
    TicketStatus,
)
from app.domain.errors import (
    InvalidTicketTransition,
    PermissionDenied,
    VersionConflict,
)
from app.domain.models import AcceptanceRejection, FailureDetails
from app.domain.tickets import (
    TicketCreationRequest,
    TicketTransitionRequest,
    plan_ticket_creation,
    transition_ticket,
)


def test_authorized_ticket_creation_starts_open_without_prior_version() -> None:
    plan = plan_ticket_creation(
        TicketCreationRequest(
            actor_type=ActorType.RESIDENT,
            resident_authorized=True,
            issue_valid=True,
            duplicate_allowed=True,
            idempotency_accepted=True,
        )
    )
    assert plan.initial_status is TicketStatus.OPEN
    assert plan.initial_version == 1
    assert plan.facts == (DomainFact.TICKET_CREATED,)


def test_ticket_creation_rejects_disallowed_duplicate() -> None:
    with pytest.raises(InvalidTicketTransition):
        plan_ticket_creation(
            TicketCreationRequest(
                actor_type=ActorType.RESIDENT,
                resident_authorized=True,
                issue_valid=True,
                duplicate_allowed=False,
                idempotency_accepted=True,
            )
        )


def _request(
    status: TicketStatus,
    action: TicketAction,
    actor: ActorType,
) -> TicketTransitionRequest:
    failure: FailureDetails | None = None
    if action is TicketAction.FAIL_WORK_REWORK:
        failure = FailureDetails(FailureReason.REPAIR_INCOMPLETE, "needs another visit", ("e1",))
    elif action is TicketAction.FAIL_WORK_ESCALATE:
        failure = FailureDetails(FailureReason.SAFETY_RISK, "unsafe panel", ("e1",))
    cancellation_reason: CancellationReason | None = None
    if action is TicketAction.CANCEL_TICKET:
        cancellation_reason = (
            CancellationReason.RESIDENT_CANCELLED
            if actor is ActorType.RESIDENT
            else CancellationReason.OPERATOR_CANCELLED
        )
    elif action in {
        TicketAction.WORKER_REJECTED_INITIAL,
        TicketAction.WORKER_REJECTED_REWORK,
    }:
        cancellation_reason = CancellationReason.WORKER_REJECTED
    elif action in {
        TicketAction.WORKER_CANCELLED_INITIAL,
        TicketAction.WORKER_CANCELLED_REWORK,
    }:
        cancellation_reason = CancellationReason.WORKER_CANCELLED
    elif action in {
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_INITIAL,
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_REWORK,
    }:
        cancellation_reason = CancellationReason.RESIDENT_CANCELLED
    booking_actions = {
        TicketAction.BOOK_APPOINTMENT,
        TicketAction.START_WORK,
        TicketAction.RESCHEDULE,
        TicketAction.WORKER_REJECTED_INITIAL,
        TicketAction.WORKER_REJECTED_REWORK,
        TicketAction.WORKER_CANCELLED_INITIAL,
        TicketAction.WORKER_CANCELLED_REWORK,
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_INITIAL,
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_REWORK,
        TicketAction.REVIEWED_NO_SHOW,
        TicketAction.COMPLETE_WORK,
        TicketAction.FAIL_WORK_REWORK,
        TicketAction.FAIL_WORK_ESCALATE,
    }
    if status is TicketStatus.SCHEDULED and action is TicketAction.CANCEL_TICKET:
        booking_actions.add(TicketAction.CANCEL_TICKET)
    rework_context = status is TicketStatus.REWORK_REQUIRED or action in {
        TicketAction.WORKER_REJECTED_REWORK,
        TicketAction.WORKER_CANCELLED_REWORK,
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_REWORK,
    }
    return TicketTransitionRequest(
        current_status=status,
        action=action,
        actor_type=actor,
        expected_version=3,
        actual_version=3,
        has_active_booked_appointment=action in booking_actions,
        cancellation_reason=cancellation_reason,
        no_show_reason=NoShowReason.WORKER_NO_SHOW
        if action is TicketAction.REVIEWED_NO_SHOW
        else None,
        escalation_reason="review required" if action is TicketAction.ESCALATE else None,
        evidence=("e1",)
        if action in {TicketAction.ESCALATE, TicketAction.REVIEWED_NO_SHOW}
        else (),
        resident_acceptance=True if action is TicketAction.RESIDENT_ACCEPT else None,
        acceptance_rejection=AcceptanceRejection(AcceptanceRejectionReason.ISSUE_NOT_RESOLVED)
        if action is TicketAction.RESIDENT_REJECT
        else None,
        failure=failure,
        rework_recorded=status is TicketStatus.REWORK_REQUIRED,
        current_rework_count=1 if rework_context else 0,
    )


@pytest.mark.parametrize(
    ("status", "action", "actor", "expected"),
    [
        (
            TicketStatus.OPEN,
            TicketAction.BOOK_APPOINTMENT,
            ActorType.RESIDENT,
            TicketStatus.SCHEDULED,
        ),
        (TicketStatus.OPEN, TicketAction.CANCEL_TICKET, ActorType.RESIDENT, TicketStatus.CANCELLED),
        (
            TicketStatus.SCHEDULED,
            TicketAction.START_WORK,
            ActorType.WORKER,
            TicketStatus.IN_PROGRESS,
        ),
        (
            TicketStatus.SCHEDULED,
            TicketAction.RESCHEDULE,
            ActorType.RESIDENT,
            TicketStatus.SCHEDULED,
        ),
        (
            TicketStatus.SCHEDULED,
            TicketAction.WORKER_REJECTED_INITIAL,
            ActorType.WORKER,
            TicketStatus.OPEN,
        ),
        (
            TicketStatus.SCHEDULED,
            TicketAction.WORKER_REJECTED_REWORK,
            ActorType.WORKER,
            TicketStatus.REWORK_REQUIRED,
        ),
        (
            TicketStatus.SCHEDULED,
            TicketAction.WORKER_CANCELLED_INITIAL,
            ActorType.WORKER,
            TicketStatus.OPEN,
        ),
        (
            TicketStatus.SCHEDULED,
            TicketAction.WORKER_CANCELLED_REWORK,
            ActorType.WORKER,
            TicketStatus.REWORK_REQUIRED,
        ),
        (
            TicketStatus.SCHEDULED,
            TicketAction.RESIDENT_CANCELLED_APPOINTMENT_INITIAL,
            ActorType.RESIDENT,
            TicketStatus.OPEN,
        ),
        (
            TicketStatus.SCHEDULED,
            TicketAction.RESIDENT_CANCELLED_APPOINTMENT_REWORK,
            ActorType.RESIDENT,
            TicketStatus.REWORK_REQUIRED,
        ),
        (
            TicketStatus.SCHEDULED,
            TicketAction.CANCEL_TICKET,
            ActorType.RESIDENT,
            TicketStatus.CANCELLED,
        ),
        (
            TicketStatus.SCHEDULED,
            TicketAction.REVIEWED_NO_SHOW,
            ActorType.OPERATOR,
            TicketStatus.ESCALATED,
        ),
        (
            TicketStatus.SCHEDULED,
            TicketAction.RESIDENT_ACCEPT,
            ActorType.RESIDENT,
            TicketStatus.CLOSED,
        ),
        (
            TicketStatus.IN_PROGRESS,
            TicketAction.RESIDENT_ACCEPT,
            ActorType.RESIDENT,
            TicketStatus.CLOSED,
        ),
        (
            TicketStatus.IN_PROGRESS,
            TicketAction.COMPLETE_WORK,
            ActorType.WORKER,
            TicketStatus.PENDING_ACCEPTANCE,
        ),
        (
            TicketStatus.IN_PROGRESS,
            TicketAction.FAIL_WORK_REWORK,
            ActorType.WORKER,
            TicketStatus.REWORK_REQUIRED,
        ),
        (
            TicketStatus.IN_PROGRESS,
            TicketAction.FAIL_WORK_ESCALATE,
            ActorType.WORKER,
            TicketStatus.ESCALATED,
        ),
        (
            TicketStatus.PENDING_ACCEPTANCE,
            TicketAction.RESIDENT_ACCEPT,
            ActorType.RESIDENT,
            TicketStatus.CLOSED,
        ),
        (
            TicketStatus.PENDING_ACCEPTANCE,
            TicketAction.RESIDENT_REJECT,
            ActorType.RESIDENT,
            TicketStatus.REWORK_REQUIRED,
        ),
        (
            TicketStatus.REWORK_REQUIRED,
            TicketAction.BOOK_APPOINTMENT,
            ActorType.OPERATOR,
            TicketStatus.SCHEDULED,
        ),
        (
            TicketStatus.REWORK_REQUIRED,
            TicketAction.CANCEL_TICKET,
            ActorType.OPERATOR,
            TicketStatus.CANCELLED,
        ),
    ],
)
def test_all_non_escalation_matrix_rows(
    status: TicketStatus,
    action: TicketAction,
    actor: ActorType,
    expected: TicketStatus,
) -> None:
    result = transition_ticket(_request(status, action, actor))
    assert result.next_status is expected
    assert result.next_version == 4
    assert DomainFact.TICKET_TRANSITIONED in result.facts


@pytest.mark.parametrize(
    "status",
    [
        TicketStatus.OPEN,
        TicketStatus.SCHEDULED,
        TicketStatus.IN_PROGRESS,
        TicketStatus.PENDING_ACCEPTANCE,
        TicketStatus.REWORK_REQUIRED,
    ],
)
def test_all_five_states_can_escalate(status: TicketStatus) -> None:
    result = transition_ticket(_request(status, TicketAction.ESCALATE, ActorType.SYSTEM))
    assert result.next_status is TicketStatus.ESCALATED
    assert result.escalated_from_status is status


@pytest.mark.parametrize("status", [TicketStatus.CANCELLED, TicketStatus.CLOSED])
@pytest.mark.parametrize("action", list(TicketAction))
def test_terminal_states_have_no_escape_path(status: TicketStatus, action: TicketAction) -> None:
    with pytest.raises(InvalidTicketTransition):
        transition_ticket(_request(status, action, ActorType.OPERATOR))


def test_version_conflict() -> None:
    request = _request(TicketStatus.OPEN, TicketAction.BOOK_APPOINTMENT, ActorType.RESIDENT)
    stale = TicketTransitionRequest(
        current_status=request.current_status,
        action=request.action,
        actor_type=request.actor_type,
        expected_version=2,
        actual_version=3,
        has_active_booked_appointment=True,
    )
    with pytest.raises(VersionConflict):
        transition_ticket(stale)


def test_version_conflict_precedes_business_effect_calculation() -> None:
    stale_and_incomplete = TicketTransitionRequest(
        current_status=TicketStatus.PENDING_ACCEPTANCE,
        action=TicketAction.RESIDENT_REJECT,
        actor_type=ActorType.RESIDENT,
        expected_version=3,
        actual_version=4,
        current_rework_count=2,
    )
    with pytest.raises(VersionConflict):
        transition_ticket(stale_and_incomplete)


def test_stale_rework_retry_cannot_increment_count_twice() -> None:
    first = _request(
        TicketStatus.PENDING_ACCEPTANCE,
        TicketAction.RESIDENT_REJECT,
        ActorType.RESIDENT,
    )
    result = transition_ticket(first)
    assert result.next_rework_count == 1
    retry_after_commit = TicketTransitionRequest(
        current_status=TicketStatus.PENDING_ACCEPTANCE,
        action=TicketAction.RESIDENT_REJECT,
        actor_type=ActorType.RESIDENT,
        expected_version=3,
        actual_version=4,
        acceptance_rejection=AcceptanceRejection(AcceptanceRejectionReason.ISSUE_NOT_RESOLVED),
        current_rework_count=1,
    )
    with pytest.raises(VersionConflict):
        transition_ticket(retry_after_commit)


def test_operator_cannot_accept_for_resident() -> None:
    request = _request(
        TicketStatus.PENDING_ACCEPTANCE,
        TicketAction.RESIDENT_ACCEPT,
        ActorType.OPERATOR,
    )
    with pytest.raises(PermissionDenied):
        transition_ticket(request)


@pytest.mark.parametrize(
    ("status", "action", "allowed", "denied"),
    [
        (TicketStatus.OPEN, TicketAction.BOOK_APPOINTMENT, ActorType.OPERATOR, ActorType.WORKER),
        (
            TicketStatus.SCHEDULED,
            TicketAction.START_WORK,
            ActorType.OPERATOR,
            ActorType.RESIDENT,
        ),
        (TicketStatus.OPEN, TicketAction.ESCALATE, ActorType.OPERATOR, ActorType.RESIDENT),
        (
            TicketStatus.REWORK_REQUIRED,
            TicketAction.CANCEL_TICKET,
            ActorType.OPERATOR,
            ActorType.RESIDENT,
        ),
    ],
)
def test_role_sets_have_positive_and_negative_examples(
    status: TicketStatus,
    action: TicketAction,
    allowed: ActorType,
    denied: ActorType,
) -> None:
    transition_ticket(_request(status, action, allowed))
    with pytest.raises(PermissionDenied):
        transition_ticket(_request(status, action, denied))


def test_missing_required_context_is_rejected() -> None:
    request = TicketTransitionRequest(
        current_status=TicketStatus.OPEN,
        action=TicketAction.BOOK_APPOINTMENT,
        actor_type=ActorType.RESIDENT,
        expected_version=1,
        actual_version=1,
    )
    with pytest.raises(InvalidTicketTransition):
        transition_ticket(request)


def test_skipping_states_is_rejected() -> None:
    with pytest.raises(InvalidTicketTransition):
        transition_ticket(
            _request(TicketStatus.OPEN, TicketAction.RESIDENT_ACCEPT, ActorType.RESIDENT)
        )


def test_unauthorized_resident_is_rejected() -> None:
    request = TicketTransitionRequest(
        current_status=TicketStatus.OPEN,
        action=TicketAction.BOOK_APPOINTMENT,
        actor_type=ActorType.RESIDENT,
        expected_version=1,
        actual_version=1,
        resident_authorized=False,
        has_active_booked_appointment=True,
    )
    with pytest.raises(PermissionDenied):
        transition_ticket(request)

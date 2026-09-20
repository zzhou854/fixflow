"""Complete normal and same-ticket rework domain flows."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.enums import (
    AcceptanceRejectionReason,
    ActorType,
    AppointmentPurpose,
    AppointmentStatus,
    TicketAction,
    TicketStatus,
    WorkerEventType,
)
from app.domain.invariants import plan_rework_appointment
from app.domain.models import (
    AcceptanceRejection,
    AppointmentDraft,
    AppointmentSnapshot,
)
from app.domain.tickets import TicketTransitionRequest, transition_ticket
from app.domain.worker_events import WorkerEventRequest, validate_worker_event

NOW = datetime(2026, 7, 17, 9, tzinfo=UTC)


def _ticket_transition(
    status: TicketStatus,
    action: TicketAction,
    actor: ActorType,
    version: int,
    *,
    booking: bool = False,
    rejection: AcceptanceRejection | None = None,
    rework_recorded: bool = False,
    acceptance: bool | None = None,
    current_rework_count: int = 0,
) -> tuple[TicketStatus, int, int]:
    result = transition_ticket(
        TicketTransitionRequest(
            current_status=status,
            action=action,
            actor_type=actor,
            expected_version=version,
            actual_version=version,
            has_active_booked_appointment=booking,
            acceptance_rejection=rejection,
            rework_recorded=rework_recorded,
            resident_acceptance=acceptance,
            current_rework_count=current_rework_count,
        )
    )
    return result.next_status, result.next_version, result.next_rework_count


def test_complete_normal_flow_waits_for_resident_acceptance() -> None:
    status, version, _ = _ticket_transition(
        TicketStatus.OPEN,
        TicketAction.BOOK_APPOINTMENT,
        ActorType.RESIDENT,
        1,
        booking=True,
    )
    status, version, _ = _ticket_transition(
        status, TicketAction.START_WORK, ActorType.WORKER, version, booking=True
    )
    completed = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.COMPLETED,
            actor_type=ActorType.WORKER,
            appointment_id=UUID(int=10),
            ticket_status=status,
            appointment_status=AppointmentStatus.BOOKED,
            prior_events=(WorkerEventType.STARTED,),
        )
    )
    status = completed.next_ticket_status
    assert status is TicketStatus.PENDING_ACCEPTANCE
    assert completed.next_appointment_status is AppointmentStatus.FULFILLED
    status, version, _ = _ticket_transition(
        status,
        TicketAction.RESIDENT_ACCEPT,
        ActorType.RESIDENT,
        version,
        acceptance=True,
    )
    assert status is TicketStatus.CLOSED
    assert version == 4


def test_complete_rework_flow_reuses_ticket_and_creates_new_appointment() -> None:
    ticket_id = UUID(int=1)
    first_appointment_id = UUID(int=2)
    status, version, _ = _ticket_transition(
        TicketStatus.OPEN,
        TicketAction.BOOK_APPOINTMENT,
        ActorType.RESIDENT,
        1,
        booking=True,
    )
    status, version, _ = _ticket_transition(
        status, TicketAction.START_WORK, ActorType.WORKER, version, booking=True
    )
    status, version, _ = _ticket_transition(
        status, TicketAction.COMPLETE_WORK, ActorType.WORKER, version, booking=True
    )
    status, version, rework_count = _ticket_transition(
        status,
        TicketAction.RESIDENT_REJECT,
        ActorType.RESIDENT,
        version,
        rejection=AcceptanceRejection(AcceptanceRejectionReason.ISSUE_NOT_RESOLVED),
    )
    assert status is TicketStatus.REWORK_REQUIRED
    assert rework_count == 1

    old = AppointmentSnapshot(
        first_appointment_id,
        ticket_id,
        UUID(int=3),
        AppointmentPurpose.INITIAL_REPAIR,
        NOW,
        NOW + timedelta(hours=1),
        AppointmentStatus.FULFILLED,
        2,
    )
    replacement = AppointmentDraft(
        UUID(int=4),
        ticket_id,
        UUID(int=5),
        AppointmentPurpose.REWORK,
        NOW + timedelta(days=1),
        NOW + timedelta(days=1, hours=1),
    )
    plan = plan_rework_appointment(
        ticket_id=ticket_id,
        current_status=status,
        current_rework_count=1,
        prior_appointment=old,
        replacement=replacement,
    )
    assert plan.ticket_id == ticket_id
    assert plan.prior_appointment_id == first_appointment_id
    assert plan.new_appointment.appointment_id != first_appointment_id
    assert old.status is AppointmentStatus.FULFILLED
    assert plan.next_ticket_status is TicketStatus.SCHEDULED
    assert plan.next_rework_count == rework_count
    assert plan.new_appointment.supersedes_appointment_id is None

    status, version, _ = _ticket_transition(
        status,
        TicketAction.BOOK_APPOINTMENT,
        ActorType.RESIDENT,
        version,
        booking=True,
        rework_recorded=True,
        current_rework_count=rework_count,
    )
    status, version, _ = _ticket_transition(
        status, TicketAction.START_WORK, ActorType.WORKER, version, booking=True
    )
    status, version, _ = _ticket_transition(
        status, TicketAction.COMPLETE_WORK, ActorType.WORKER, version, booking=True
    )
    status, _, _ = _ticket_transition(
        status,
        TicketAction.RESIDENT_ACCEPT,
        ActorType.RESIDENT,
        version,
        acceptance=True,
    )
    assert status is TicketStatus.CLOSED

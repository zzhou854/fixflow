"""Reusable cross-entity business invariants for FixFlow."""

from dataclasses import dataclass
from uuid import UUID

from app.domain.enums import (
    TERMINAL_APPOINTMENT_STATUSES,
    AppointmentPurpose,
    AppointmentStatus,
    DomainFact,
    TicketStatus,
    WorkerEventType,
)
from app.domain.errors import InvariantViolation, PermissionDenied, VersionConflict
from app.domain.models import AppointmentDraft, AppointmentSnapshot


def ensure_expected_version(expected: int, actual: int) -> None:
    """Reject a mutation evaluated against a stale aggregate version."""

    if expected != actual:
        raise VersionConflict("version_conflict", expected=expected, actual=actual)


def ensure_single_active_appointment(appointments: tuple[AppointmentSnapshot, ...]) -> None:
    """Ensure a ticket has at most one active BOOKED appointment."""

    booked_by_ticket: dict[UUID, int] = {}
    for appointment in appointments:
        if appointment.status is AppointmentStatus.BOOKED:
            count = booked_by_ticket.get(appointment.ticket_id, 0) + 1
            booked_by_ticket[appointment.ticket_id] = count
            if count > 1:
                raise InvariantViolation(
                    "multiple_active_appointments", ticket_id=appointment.ticket_id
                )


def ensure_no_worker_overlap(appointments: tuple[AppointmentSnapshot, ...]) -> None:
    """Detect overlapping active bookings in an in-memory domain snapshot."""

    active = sorted(
        (item for item in appointments if item.status is AppointmentStatus.BOOKED),
        key=lambda item: (str(item.worker_id), item.starts_at),
    )
    for left, right in zip(active, active[1:], strict=False):
        if left.worker_id == right.worker_id and left.ends_at > right.starts_at:
            raise InvariantViolation(
                "worker_booking_overlap",
                worker_id=left.worker_id,
                left_appointment_id=left.appointment_id,
                right_appointment_id=right.appointment_id,
            )


def ensure_resident_authorized(*, authorized: bool, ticket_id: UUID) -> None:
    """Reject a resident command for an unrelated property or ticket."""

    if not authorized:
        raise PermissionDenied("resident_not_authorized", ticket_id=ticket_id)


def ensure_ticket_can_close(current_status: TicketStatus, *, resident_acceptance: bool) -> None:
    """Require the acceptance state and explicit resident decision before closure."""

    if current_status is not TicketStatus.PENDING_ACCEPTANCE or not resident_acceptance:
        raise InvariantViolation(
            "ticket_cannot_close",
            current_status=current_status,
            resident_acceptance=resident_acceptance,
        )


def ensure_ticket_accepts_worker_event(
    ticket_status: TicketStatus, event_type: WorkerEventType
) -> None:
    """Prevent ordinary worker activity on closed or cancelled tickets."""

    if ticket_status in {TicketStatus.CLOSED, TicketStatus.CANCELLED}:
        raise InvariantViolation(
            "terminal_ticket_rejects_worker_event",
            current_status=ticket_status,
            event=event_type,
        )


def ensure_ticket_can_book(ticket_status: TicketStatus) -> None:
    """Allow new booking only while open or explicitly awaiting rework."""

    if ticket_status not in {TicketStatus.OPEN, TicketStatus.REWORK_REQUIRED}:
        raise InvariantViolation("ticket_cannot_book", current_status=ticket_status)


def ensure_appointment_core_unchanged(
    original: AppointmentSnapshot, proposed: AppointmentSnapshot
) -> None:
    """Protect terminal appointment time, worker, ticket, and recorded state."""

    if original.status not in TERMINAL_APPOINTMENT_STATUSES:
        return
    original_core = (
        original.ticket_id,
        original.worker_id,
        original.purpose,
        original.starts_at,
        original.ends_at,
        original.status,
        original.supersedes_appointment_id,
    )
    proposed_core = (
        proposed.ticket_id,
        proposed.worker_id,
        proposed.purpose,
        proposed.starts_at,
        proposed.ends_at,
        proposed.status,
        proposed.supersedes_appointment_id,
    )
    if original_core != proposed_core:
        raise InvariantViolation(
            "terminal_appointment_core_changed", appointment_id=original.appointment_id
        )


def ensure_resident_rejection_routes_to_rework(next_status: TicketStatus) -> None:
    """Ensure rejected acceptance retains the same ticket for rework."""

    if next_status is not TicketStatus.REWORK_REQUIRED:
        raise InvariantViolation("resident_rejection_must_rework", next_status=next_status)


def ensure_worker_completion_does_not_close(next_status: TicketStatus) -> None:
    """Ensure a worker completion claim cannot close a repair ticket."""

    if next_status is TicketStatus.CLOSED:
        raise InvariantViolation("worker_completion_cannot_close")


def ensure_aggregate_consistency(
    ticket_status: TicketStatus,
    appointments: tuple[AppointmentSnapshot, ...],
    latest_worker_event: WorkerEventType | None,
) -> None:
    """Stop automation when ticket, appointment, and event facts disagree."""

    active = [item for item in appointments if item.status is AppointmentStatus.BOOKED]
    fulfilled = [item for item in appointments if item.status is AppointmentStatus.FULFILLED]
    if len(active) > 1:
        raise InvariantViolation("multiple_active_appointments")
    if ticket_status is TicketStatus.SCHEDULED and len(active) != 1:
        raise InvariantViolation("scheduled_ticket_requires_booking")
    if ticket_status is TicketStatus.IN_PROGRESS and (
        len(active) != 1 or latest_worker_event is not WorkerEventType.STARTED
    ):
        raise InvariantViolation("in_progress_snapshot_conflict")
    if ticket_status is TicketStatus.PENDING_ACCEPTANCE and (
        not fulfilled or latest_worker_event is not WorkerEventType.COMPLETED
    ):
        raise InvariantViolation("acceptance_snapshot_conflict")
    if (
        ticket_status
        in {
            TicketStatus.OPEN,
            TicketStatus.REWORK_REQUIRED,
            TicketStatus.CANCELLED,
            TicketStatus.CLOSED,
        }
        and active
    ):
        raise InvariantViolation(
            "ticket_status_conflicts_with_booking", current_status=ticket_status
        )


@dataclass(frozen=True, slots=True)
class ReworkPlan:
    """Same-ticket plan for a new appointment after a completed attempt."""

    ticket_id: UUID
    prior_appointment_id: UUID
    new_appointment: AppointmentDraft
    next_ticket_status: TicketStatus
    next_rework_count: int
    facts: tuple[DomainFact, ...] = (DomainFact.REWORK_APPOINTMENT_PLANNED,)


def plan_rework_appointment(
    *,
    ticket_id: UUID,
    current_status: TicketStatus,
    current_rework_count: int,
    prior_appointment: AppointmentSnapshot,
    replacement: AppointmentDraft,
) -> ReworkPlan:
    """Create an indivisible same-ticket rework booking plan."""

    if current_status is not TicketStatus.REWORK_REQUIRED:
        raise InvariantViolation("ticket_not_ready_for_rework", current_status=current_status)
    if current_rework_count < 1:
        raise InvariantViolation("rework_history_required")
    if prior_appointment.ticket_id != ticket_id or replacement.ticket_id != ticket_id:
        raise InvariantViolation("rework_must_reuse_ticket", ticket_id=ticket_id)
    if prior_appointment.status not in TERMINAL_APPOINTMENT_STATUSES:
        raise InvariantViolation("prior_appointment_must_be_terminal")
    if replacement.status is not AppointmentStatus.BOOKED:
        raise InvariantViolation("rework_replacement_must_be_booked")
    if replacement.purpose is not AppointmentPurpose.REWORK:
        raise InvariantViolation("rework_appointment_purpose_required")
    if replacement.appointment_id == prior_appointment.appointment_id:
        raise InvariantViolation("rework_requires_new_appointment")
    if replacement.supersedes_appointment_id is not None:
        raise InvariantViolation("rework_is_not_reschedule")
    return ReworkPlan(
        ticket_id=ticket_id,
        prior_appointment_id=prior_appointment.appointment_id,
        new_appointment=replacement,
        next_ticket_status=TicketStatus.SCHEDULED,
        next_rework_count=current_rework_count,
    )

"""Frozen business-invariant tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.domain.enums import AppointmentPurpose, AppointmentStatus, TicketStatus, WorkerEventType
from app.domain.errors import InvariantViolation, PermissionDenied, VersionConflict
from app.domain.invariants import (
    ensure_aggregate_consistency,
    ensure_appointment_core_unchanged,
    ensure_expected_version,
    ensure_no_worker_overlap,
    ensure_resident_authorized,
    ensure_resident_rejection_routes_to_rework,
    ensure_single_active_appointment,
    ensure_ticket_accepts_worker_event,
    ensure_ticket_can_book,
    ensure_ticket_can_close,
    ensure_worker_completion_does_not_close,
)
from app.domain.models import AppointmentSnapshot

NOW = datetime(2026, 7, 17, 9, tzinfo=UTC)


def _appointment(
    identity: int,
    *,
    ticket: int = 1,
    worker: int = 2,
    start_hours: int = 0,
    status: AppointmentStatus = AppointmentStatus.BOOKED,
) -> AppointmentSnapshot:
    start = NOW + timedelta(hours=start_hours)
    return AppointmentSnapshot(
        UUID(int=identity),
        UUID(int=ticket),
        UUID(int=worker),
        AppointmentPurpose.INITIAL_REPAIR,
        start,
        start + timedelta(hours=2),
        status,
        1,
    )


def test_ticket_has_at_most_one_booked_appointment() -> None:
    with pytest.raises(InvariantViolation):
        ensure_single_active_appointment((_appointment(1), _appointment(2)))


def test_worker_cannot_have_overlapping_bookings() -> None:
    with pytest.raises(InvariantViolation):
        ensure_no_worker_overlap((_appointment(1), _appointment(2, ticket=2, start_hours=1)))
    ensure_no_worker_overlap((_appointment(1), _appointment(2, ticket=2, start_hours=2)))


def test_ticket_cannot_close_before_acceptance() -> None:
    with pytest.raises(InvariantViolation):
        ensure_ticket_can_close(TicketStatus.IN_PROGRESS, resident_acceptance=True)
    with pytest.raises(InvariantViolation):
        ensure_ticket_can_close(TicketStatus.PENDING_ACCEPTANCE, resident_acceptance=False)
    ensure_ticket_can_close(TicketStatus.PENDING_ACCEPTANCE, resident_acceptance=True)


def test_worker_completed_cannot_close_ticket() -> None:
    with pytest.raises(InvariantViolation):
        ensure_worker_completion_does_not_close(TicketStatus.CLOSED)
    ensure_worker_completion_does_not_close(TicketStatus.PENDING_ACCEPTANCE)


def test_resident_rejection_must_enter_rework() -> None:
    with pytest.raises(InvariantViolation):
        ensure_resident_rejection_routes_to_rework(TicketStatus.OPEN)
    ensure_resident_rejection_routes_to_rework(TicketStatus.REWORK_REQUIRED)


@pytest.mark.parametrize("status", [TicketStatus.CLOSED, TicketStatus.CANCELLED])
def test_terminal_ticket_rejects_worker_events(status: TicketStatus) -> None:
    with pytest.raises(InvariantViolation):
        ensure_ticket_accepts_worker_event(status, WorkerEventType.STARTED)


def test_cancelled_ticket_cannot_book() -> None:
    with pytest.raises(InvariantViolation):
        ensure_ticket_can_book(TicketStatus.CANCELLED)
    ensure_ticket_can_book(TicketStatus.OPEN)
    ensure_ticket_can_book(TicketStatus.REWORK_REQUIRED)


def test_terminal_appointment_core_is_immutable() -> None:
    original = _appointment(1, status=AppointmentStatus.SUPERSEDED)
    changed = replace(original, worker_id=UUID(int=99))
    with pytest.raises(InvariantViolation):
        ensure_appointment_core_unchanged(original, changed)


def test_expected_version_must_match() -> None:
    with pytest.raises(VersionConflict):
        ensure_expected_version(1, 2)
    ensure_expected_version(2, 2)


def test_resident_must_be_authorized() -> None:
    with pytest.raises(PermissionDenied):
        ensure_resident_authorized(authorized=False, ticket_id=UUID(int=1))
    ensure_resident_authorized(authorized=True, ticket_id=UUID(int=1))


@pytest.mark.parametrize(
    ("status", "appointments", "event"),
    [
        (TicketStatus.SCHEDULED, (), None),
        (TicketStatus.IN_PROGRESS, (_appointment(1),), WorkerEventType.ARRIVED),
        (
            TicketStatus.PENDING_ACCEPTANCE,
            (_appointment(1, status=AppointmentStatus.FULFILLED),),
            WorkerEventType.STARTED,
        ),
        (TicketStatus.OPEN, (_appointment(1),), None),
    ],
)
def test_inconsistent_aggregates_stop_automatic_progress(
    status: TicketStatus,
    appointments: tuple[AppointmentSnapshot, ...],
    event: WorkerEventType | None,
) -> None:
    with pytest.raises(InvariantViolation):
        ensure_aggregate_consistency(status, appointments, event)


def test_consistent_aggregate_snapshots_pass() -> None:
    ensure_aggregate_consistency(TicketStatus.SCHEDULED, (_appointment(1),), None)
    ensure_aggregate_consistency(
        TicketStatus.IN_PROGRESS, (_appointment(1),), WorkerEventType.STARTED
    )
    ensure_aggregate_consistency(
        TicketStatus.PENDING_ACCEPTANCE,
        (_appointment(1, status=AppointmentStatus.FULFILLED),),
        WorkerEventType.COMPLETED,
    )

"""Escalation entry and safe recovery tests."""

import pytest
from app.domain.enums import (
    ActorType,
    AppointmentStatus,
    EscalationDisposition,
    TicketStatus,
    WorkerEventType,
)
from app.domain.errors import InvalidEscalationRecovery, PermissionDenied, VersionConflict
from app.domain.tickets import EscalationRecoveryRequest, recover_escalated_ticket


@pytest.mark.parametrize(
    ("prior", "appointment", "event", "expected"),
    [
        (TicketStatus.OPEN, None, None, TicketStatus.OPEN),
        (
            TicketStatus.SCHEDULED,
            AppointmentStatus.BOOKED,
            WorkerEventType.ACCEPTED,
            TicketStatus.SCHEDULED,
        ),
        (
            TicketStatus.IN_PROGRESS,
            AppointmentStatus.BOOKED,
            WorkerEventType.STARTED,
            TicketStatus.IN_PROGRESS,
        ),
        (
            TicketStatus.PENDING_ACCEPTANCE,
            AppointmentStatus.FULFILLED,
            WorkerEventType.COMPLETED,
            TicketStatus.PENDING_ACCEPTANCE,
        ),
    ],
)
def test_resume_target_is_derived_from_current_facts(
    prior: TicketStatus,
    appointment: AppointmentStatus | None,
    event: WorkerEventType | None,
    expected: TicketStatus,
) -> None:
    result = recover_escalated_ticket(
        EscalationRecoveryRequest(
            actor_type=ActorType.OPERATOR,
            expected_version=5,
            actual_version=5,
            escalated_from_status=prior,
            appointment_status=appointment,
            latest_worker_event=event,
            disposition=EscalationDisposition.RESUME,
        )
    )
    assert result.next_status is expected
    assert result.next_version == 6


def test_reviewed_rework_recovery() -> None:
    result = recover_escalated_ticket(
        EscalationRecoveryRequest(
            actor_type=ActorType.OPERATOR,
            expected_version=2,
            actual_version=2,
            escalated_from_status=TicketStatus.IN_PROGRESS,
            appointment_status=AppointmentStatus.FULFILLED,
            latest_worker_event=WorkerEventType.FAILED_TO_COMPLETE,
            disposition=EscalationDisposition.APPROVE_REWORK,
            rework_recorded=True,
        )
    )
    assert result.next_status is TicketStatus.REWORK_REQUIRED
    assert result.next_rework_count == 1


def test_closure_requires_existing_resident_acceptance() -> None:
    with pytest.raises(InvalidEscalationRecovery):
        recover_escalated_ticket(
            EscalationRecoveryRequest(
                actor_type=ActorType.OPERATOR,
                expected_version=2,
                actual_version=2,
                escalated_from_status=TicketStatus.PENDING_ACCEPTANCE,
                appointment_status=AppointmentStatus.FULFILLED,
                latest_worker_event=WorkerEventType.COMPLETED,
                disposition=EscalationDisposition.APPLY_RESIDENT_ACCEPTANCE,
            )
        )
    result = recover_escalated_ticket(
        EscalationRecoveryRequest(
            actor_type=ActorType.OPERATOR,
            expected_version=2,
            actual_version=2,
            escalated_from_status=TicketStatus.PENDING_ACCEPTANCE,
            appointment_status=AppointmentStatus.FULFILLED,
            latest_worker_event=WorkerEventType.COMPLETED,
            disposition=EscalationDisposition.APPLY_RESIDENT_ACCEPTANCE,
            resident_acceptance=True,
        )
    )
    assert result.next_status is TicketStatus.CLOSED


def test_reviewed_safe_cancellation() -> None:
    result = recover_escalated_ticket(
        EscalationRecoveryRequest(
            actor_type=ActorType.OPERATOR,
            expected_version=1,
            actual_version=1,
            escalated_from_status=TicketStatus.REWORK_REQUIRED,
            appointment_status=AppointmentStatus.FULFILLED,
            latest_worker_event=WorkerEventType.FAILED_TO_COMPLETE,
            disposition=EscalationDisposition.CANCEL,
        )
    )
    assert result.next_status is TicketStatus.CANCELLED


@pytest.mark.parametrize("prior", [TicketStatus.CANCELLED, TicketStatus.CLOSED, None])
def test_invalid_escalated_from_status(prior: TicketStatus | None) -> None:
    with pytest.raises(InvalidEscalationRecovery):
        recover_escalated_ticket(
            EscalationRecoveryRequest(
                actor_type=ActorType.OPERATOR,
                expected_version=1,
                actual_version=1,
                escalated_from_status=prior,
                appointment_status=None,
                latest_worker_event=None,
                disposition=EscalationDisposition.RESUME,
            )
        )


def test_unresolved_conflict_stays_escalated() -> None:
    with pytest.raises(InvalidEscalationRecovery):
        recover_escalated_ticket(
            EscalationRecoveryRequest(
                actor_type=ActorType.OPERATOR,
                expected_version=1,
                actual_version=1,
                escalated_from_status=TicketStatus.OPEN,
                appointment_status=None,
                latest_worker_event=None,
                disposition=EscalationDisposition.RESUME,
                conflict_resolved=False,
            )
        )


def test_contradictory_resume_snapshot_has_no_safe_target() -> None:
    with pytest.raises(InvalidEscalationRecovery):
        recover_escalated_ticket(
            EscalationRecoveryRequest(
                actor_type=ActorType.OPERATOR,
                expected_version=1,
                actual_version=1,
                escalated_from_status=TicketStatus.IN_PROGRESS,
                appointment_status=AppointmentStatus.BOOKED,
                latest_worker_event=WorkerEventType.ACCEPTED,
                disposition=EscalationDisposition.RESUME,
            )
        )


def test_rework_recovery_requires_reviewed_record() -> None:
    with pytest.raises(InvalidEscalationRecovery):
        recover_escalated_ticket(
            EscalationRecoveryRequest(
                actor_type=ActorType.OPERATOR,
                expected_version=1,
                actual_version=1,
                escalated_from_status=TicketStatus.IN_PROGRESS,
                appointment_status=AppointmentStatus.FULFILLED,
                latest_worker_event=WorkerEventType.FAILED_TO_COMPLETE,
                disposition=EscalationDisposition.APPROVE_REWORK,
            )
        )


def test_non_operator_cannot_recover() -> None:
    with pytest.raises(PermissionDenied):
        recover_escalated_ticket(
            EscalationRecoveryRequest(
                actor_type=ActorType.RESIDENT,
                expected_version=1,
                actual_version=1,
                escalated_from_status=TicketStatus.OPEN,
                appointment_status=None,
                latest_worker_event=None,
                disposition=EscalationDisposition.RESUME,
            )
        )


def test_stale_recovery_snapshot_is_rejected() -> None:
    with pytest.raises(VersionConflict):
        recover_escalated_ticket(
            EscalationRecoveryRequest(
                actor_type=ActorType.OPERATOR,
                expected_version=1,
                actual_version=2,
                escalated_from_status=TicketStatus.OPEN,
                appointment_status=None,
                latest_worker_event=None,
                disposition=EscalationDisposition.RESUME,
            )
        )

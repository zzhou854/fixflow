"""Canonical worker-event validation tests."""

from uuid import UUID

import pytest
from app.domain.enums import (
    ActorType,
    AppointmentPurpose,
    AppointmentStatus,
    CancellationReason,
    FailureReason,
    NoShowReason,
    TicketStatus,
    WorkerEventType,
)
from app.domain.errors import InvalidWorkerEvent, PermissionDenied, VersionConflict
from app.domain.models import FailureDetails
from app.domain.worker_events import WorkerEventRequest, validate_worker_event

APPOINTMENT_ID = UUID(int=1)


@pytest.mark.parametrize(
    ("event", "prior", "ticket", "expected_ticket"),
    [
        (WorkerEventType.ACCEPTED, (), TicketStatus.SCHEDULED, TicketStatus.SCHEDULED),
        (
            WorkerEventType.DEPARTED,
            (WorkerEventType.ACCEPTED,),
            TicketStatus.SCHEDULED,
            TicketStatus.SCHEDULED,
        ),
        (
            WorkerEventType.ARRIVED,
            (WorkerEventType.ACCEPTED, WorkerEventType.DEPARTED),
            TicketStatus.SCHEDULED,
            TicketStatus.SCHEDULED,
        ),
        (
            WorkerEventType.STARTED,
            (WorkerEventType.ACCEPTED, WorkerEventType.DEPARTED, WorkerEventType.ARRIVED),
            TicketStatus.SCHEDULED,
            TicketStatus.IN_PROGRESS,
        ),
        (
            WorkerEventType.COMPLETED,
            (
                WorkerEventType.ACCEPTED,
                WorkerEventType.DEPARTED,
                WorkerEventType.ARRIVED,
                WorkerEventType.STARTED,
            ),
            TicketStatus.IN_PROGRESS,
            TicketStatus.PENDING_ACCEPTANCE,
        ),
    ],
)
def test_legal_worker_event_sequence(
    event: WorkerEventType,
    prior: tuple[WorkerEventType, ...],
    ticket: TicketStatus,
    expected_ticket: TicketStatus,
) -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=event,
            actor_type=ActorType.WORKER,
            appointment_id=APPOINTMENT_ID,
            ticket_status=ticket,
            appointment_status=AppointmentStatus.BOOKED,
            prior_events=prior,
        )
    )
    assert result.next_ticket_status is expected_ticket
    expected_appointment = (
        AppointmentStatus.FULFILLED
        if event is WorkerEventType.COMPLETED
        else AppointmentStatus.BOOKED
    )
    assert result.next_appointment_status is expected_appointment


def test_accepted_changes_no_aggregate_and_replay_is_idempotent() -> None:
    replay = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.ACCEPTED,
            actor_type=ActorType.WORKER,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.SCHEDULED,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_purpose=AppointmentPurpose.INITIAL_REPAIR,
            prior_events=(WorkerEventType.ACCEPTED,),
            confirmed_replay=True,
        )
    )
    assert replay.is_replay
    assert replay.facts == ()
    assert replay.next_ticket_status is TicketStatus.SCHEDULED
    assert replay.next_appointment_status is AppointmentStatus.BOOKED


def test_rejected_cancels_booking_and_restarts_scheduling() -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.REJECTED,
            actor_type=ActorType.WORKER,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.SCHEDULED,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_purpose=AppointmentPurpose.INITIAL_REPAIR,
            cancellation_reason=CancellationReason.WORKER_REJECTED,
        )
    )
    assert result.next_ticket_status is TicketStatus.OPEN
    assert result.next_appointment_status is AppointmentStatus.CANCELLED
    assert result.next_rework_count == 0
    assert result.cancellation_reason is CancellationReason.WORKER_REJECTED


def test_rework_rejection_preserves_rework_status_and_count() -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.REJECTED,
            actor_type=ActorType.WORKER,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.SCHEDULED,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_purpose=AppointmentPurpose.REWORK,
            current_rework_count=2,
            cancellation_reason=CancellationReason.WORKER_REJECTED,
        )
    )
    assert result.next_ticket_status is TicketStatus.REWORK_REQUIRED
    assert result.next_appointment_status is AppointmentStatus.CANCELLED
    assert result.next_rework_count == 2
    assert result.cancellation_reason is CancellationReason.WORKER_REJECTED


def test_rejection_requires_appointment_purpose() -> None:
    with pytest.raises(InvalidWorkerEvent) as error:
        validate_worker_event(
            WorkerEventRequest(
                event_type=WorkerEventType.REJECTED,
                actor_type=ActorType.WORKER,
                appointment_id=APPOINTMENT_ID,
                ticket_status=TicketStatus.SCHEDULED,
                appointment_status=AppointmentStatus.BOOKED,
                cancellation_reason=CancellationReason.WORKER_REJECTED,
            )
        )
    assert error.value.code == "appointment_purpose_required"


def test_rejection_rejects_purpose_snapshot_conflict() -> None:
    with pytest.raises(InvalidWorkerEvent) as error:
        validate_worker_event(
            WorkerEventRequest(
                event_type=WorkerEventType.REJECTED,
                actor_type=ActorType.WORKER,
                appointment_id=APPOINTMENT_ID,
                ticket_status=TicketStatus.SCHEDULED,
                appointment_status=AppointmentStatus.BOOKED,
                appointment_purpose=AppointmentPurpose.INITIAL_REPAIR,
                current_rework_count=1,
                cancellation_reason=CancellationReason.WORKER_REJECTED,
            )
        )
    assert error.value.code == "appointment_purpose_snapshot_conflict"
    assert error.value.context["current_rework_count"] == 1


def test_stale_rejection_versions_produce_no_domain_result() -> None:
    with pytest.raises(VersionConflict):
        validate_worker_event(
            WorkerEventRequest(
                event_type=WorkerEventType.REJECTED,
                actor_type=ActorType.WORKER,
                appointment_id=APPOINTMENT_ID,
                ticket_status=TicketStatus.SCHEDULED,
                appointment_status=AppointmentStatus.BOOKED,
                appointment_purpose=AppointmentPurpose.REWORK,
                current_rework_count=1,
                expected_ticket_version=2,
                actual_ticket_version=3,
                cancellation_reason=CancellationReason.WORKER_REJECTED,
            )
        )


def test_stale_appointment_version_produces_no_rejection_result() -> None:
    with pytest.raises(VersionConflict):
        validate_worker_event(
            WorkerEventRequest(
                event_type=WorkerEventType.REJECTED,
                actor_type=ActorType.WORKER,
                appointment_id=APPOINTMENT_ID,
                ticket_status=TicketStatus.SCHEDULED,
                appointment_status=AppointmentStatus.BOOKED,
                appointment_purpose=AppointmentPurpose.REWORK,
                current_rework_count=1,
                expected_appointment_version=4,
                actual_appointment_version=5,
                cancellation_reason=CancellationReason.WORKER_REJECTED,
            )
        )


def test_completed_never_closes_ticket() -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.COMPLETED,
            actor_type=ActorType.OPERATOR,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.IN_PROGRESS,
            appointment_status=AppointmentStatus.BOOKED,
            prior_events=(WorkerEventType.STARTED,),
        )
    )
    assert result.next_ticket_status is TicketStatus.PENDING_ACCEPTANCE
    assert result.next_appointment_status is AppointmentStatus.FULFILLED


@pytest.mark.parametrize(
    ("reason", "expected_ticket", "human"),
    [
        (FailureReason.REPAIR_INCOMPLETE, TicketStatus.REWORK_REQUIRED, False),
        (FailureReason.FOLLOW_UP_REQUIRED, TicketStatus.REWORK_REQUIRED, False),
        (FailureReason.SAFETY_RISK, TicketStatus.ESCALATED, True),
        (FailureReason.RESPONSIBILITY_CONFLICT, TicketStatus.ESCALATED, True),
        (FailureReason.SPECIAL_RESOURCE_REQUIRED, TicketStatus.ESCALATED, True),
        (FailureReason.INDETERMINATE, TicketStatus.ESCALATED, True),
    ],
)
def test_failed_to_complete_routes_by_typed_reason(
    reason: FailureReason, expected_ticket: TicketStatus, human: bool
) -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.FAILED_TO_COMPLETE,
            actor_type=ActorType.WORKER,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.IN_PROGRESS,
            appointment_status=AppointmentStatus.BOOKED,
            prior_events=(WorkerEventType.STARTED,),
            failure=FailureDetails(reason, "worker statement", ("photo-ref",)),
        )
    )
    assert result.next_ticket_status is expected_ticket
    assert result.next_appointment_status is AppointmentStatus.FULFILLED
    assert result.requires_human_escalation is human
    assert result.failure_reason is reason


@pytest.mark.parametrize("reason", list(NoShowReason))
def test_no_show_preserves_resident_or_worker_subject(reason: NoShowReason) -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.NO_SHOW,
            actor_type=ActorType.OPERATOR,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.SCHEDULED,
            appointment_status=AppointmentStatus.BOOKED,
            no_show_reason=reason,
            evidence=("reviewed-attendance-log",),
        )
    )
    assert result.next_ticket_status is TicketStatus.ESCALATED
    assert result.next_appointment_status is AppointmentStatus.NO_SHOW


@pytest.mark.parametrize(
    ("event", "prior"),
    [
        (WorkerEventType.DEPARTED, ()),
        (WorkerEventType.ARRIVED, (WorkerEventType.ACCEPTED,)),
        (WorkerEventType.COMPLETED, (WorkerEventType.ARRIVED,)),
        (WorkerEventType.REJECTED, (WorkerEventType.ACCEPTED,)),
        (WorkerEventType.REJECTED, (WorkerEventType.DEPARTED,)),
    ],
)
def test_illegal_event_order_is_rejected(
    event: WorkerEventType, prior: tuple[WorkerEventType, ...]
) -> None:
    ticket = (
        TicketStatus.IN_PROGRESS if event is WorkerEventType.COMPLETED else TicketStatus.SCHEDULED
    )
    with pytest.raises(InvalidWorkerEvent):
        validate_worker_event(
            WorkerEventRequest(
                event_type=event,
                actor_type=ActorType.WORKER,
                appointment_id=APPOINTMENT_ID,
                ticket_status=ticket,
                appointment_status=AppointmentStatus.BOOKED,
                appointment_purpose=AppointmentPurpose.INITIAL_REPAIR,
                prior_events=prior,
                cancellation_reason=CancellationReason.WORKER_REJECTED,
            )
        )


def test_operator_can_record_work_start_without_travel_tracking() -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.STARTED,
            actor_type=ActorType.OPERATOR,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.SCHEDULED,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_purpose=AppointmentPurpose.INITIAL_REPAIR,
        )
    )

    assert result.next_ticket_status is TicketStatus.IN_PROGRESS
    assert result.next_appointment_status is AppointmentStatus.BOOKED


def test_event_requires_appointment_id() -> None:
    with pytest.raises(InvalidWorkerEvent):
        validate_worker_event(
            WorkerEventRequest(
                event_type=WorkerEventType.ACCEPTED,
                actor_type=ActorType.WORKER,
                appointment_id=None,
                ticket_status=TicketStatus.SCHEDULED,
                appointment_status=AppointmentStatus.BOOKED,
            )
        )


def test_actor_must_be_allowed() -> None:
    with pytest.raises(PermissionDenied):
        validate_worker_event(
            WorkerEventRequest(
                event_type=WorkerEventType.STARTED,
                actor_type=ActorType.RESIDENT,
                appointment_id=APPOINTMENT_ID,
                ticket_status=TicketStatus.SCHEDULED,
                appointment_status=AppointmentStatus.BOOKED,
                prior_events=(WorkerEventType.ARRIVED,),
            )
        )


@pytest.mark.parametrize("actor", [ActorType.WORKER, ActorType.OPERATOR])
def test_worker_action_allows_worker_or_operator_simulation(actor: ActorType) -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.ACCEPTED,
            actor_type=actor,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.SCHEDULED,
            appointment_status=AppointmentStatus.BOOKED,
        )
    )
    assert result.next_ticket_status is TicketStatus.SCHEDULED


@pytest.mark.parametrize("actor", [ActorType.OPERATOR, ActorType.SYSTEM])
def test_reviewed_no_show_allows_operator_or_system(actor: ActorType) -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.NO_SHOW,
            actor_type=actor,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.SCHEDULED,
            appointment_status=AppointmentStatus.BOOKED,
            no_show_reason=NoShowReason.WORKER_NO_SHOW,
            evidence=("attendance-log",),
        )
    )
    assert result.requires_human_escalation


def test_worker_cancellation_preserves_rework_cycle() -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.CANCELLED,
            actor_type=ActorType.WORKER,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.SCHEDULED,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_purpose=AppointmentPurpose.REWORK,
            current_rework_count=1,
            prior_events=(WorkerEventType.ACCEPTED,),
            cancellation_reason=CancellationReason.WORKER_CANCELLED,
        )
    )
    assert result.next_ticket_status is TicketStatus.REWORK_REQUIRED
    assert result.next_appointment_status is AppointmentStatus.CANCELLED
    assert result.next_rework_count == 1


def test_worker_cancellation_of_initial_appointment_returns_open() -> None:
    result = validate_worker_event(
        WorkerEventRequest(
            event_type=WorkerEventType.CANCELLED,
            actor_type=ActorType.WORKER,
            appointment_id=APPOINTMENT_ID,
            ticket_status=TicketStatus.SCHEDULED,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_purpose=AppointmentPurpose.INITIAL_REPAIR,
            current_rework_count=0,
            prior_events=(WorkerEventType.ACCEPTED,),
            cancellation_reason=CancellationReason.WORKER_CANCELLED,
        )
    )
    assert result.next_ticket_status is TicketStatus.OPEN
    assert result.next_appointment_status is AppointmentStatus.CANCELLED
    assert result.next_rework_count == 0

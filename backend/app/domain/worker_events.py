"""Validation and cross-entity effects for canonical worker events."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from app.domain.enums import (
    ActorType,
    AppointmentPurpose,
    AppointmentStatus,
    CancellationReason,
    DomainFact,
    FailureDisposition,
    FailureReason,
    NoShowReason,
    TicketStatus,
    WorkerEventType,
    failure_disposition,
    ticket_follow_up_facts,
)
from app.domain.errors import InvalidWorkerEvent, PermissionDenied
from app.domain.invariants import ensure_expected_version
from app.domain.models import FailureDetails


@dataclass(frozen=True, slots=True)
class WorkerEventRequest:
    """Current snapshots and evidence required to validate one event.

    ``confirmed_replay`` is true only after the application layer has matched a
    stable event identity and payload. Event type or timestamp alone never proves
    duplication.
    """

    event_type: WorkerEventType
    actor_type: ActorType
    appointment_id: UUID | None
    ticket_status: TicketStatus
    appointment_status: AppointmentStatus
    appointment_purpose: AppointmentPurpose | None = None
    current_rework_count: int = 0
    expected_ticket_version: int = 1
    actual_ticket_version: int = 1
    expected_appointment_version: int = 1
    actual_appointment_version: int = 1
    prior_events: tuple[WorkerEventType, ...] = ()
    confirmed_replay: bool = False
    cancellation_reason: CancellationReason | None = None
    no_show_reason: NoShowReason | None = None
    failure: FailureDetails | None = None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkerEventResult:
    """Validated event effects for both aggregates."""

    event_type: WorkerEventType
    next_ticket_status: TicketStatus
    next_appointment_status: AppointmentStatus
    facts: tuple[DomainFact, ...]
    requires_human_escalation: bool
    is_replay: bool
    next_rework_count: int
    cancellation_reason: CancellationReason | None = None
    failure_reason: FailureReason | None = None


_WORKER_OR_OPERATOR = frozenset({ActorType.WORKER, ActorType.OPERATOR})
_EVENT_ACTORS: Mapping[WorkerEventType, frozenset[ActorType]] = MappingProxyType(
    {
        WorkerEventType.ACCEPTED: _WORKER_OR_OPERATOR,
        WorkerEventType.REJECTED: _WORKER_OR_OPERATOR,
        WorkerEventType.DEPARTED: _WORKER_OR_OPERATOR,
        WorkerEventType.ARRIVED: _WORKER_OR_OPERATOR,
        WorkerEventType.STARTED: _WORKER_OR_OPERATOR,
        WorkerEventType.COMPLETED: _WORKER_OR_OPERATOR,
        WorkerEventType.FAILED_TO_COMPLETE: _WORKER_OR_OPERATOR,
        WorkerEventType.CANCELLED: _WORKER_OR_OPERATOR,
        WorkerEventType.NO_SHOW: frozenset({ActorType.OPERATOR, ActorType.SYSTEM}),
    }
)

_PRE_WORK_EVENTS = frozenset(
    {
        WorkerEventType.ACCEPTED,
        WorkerEventType.REJECTED,
        WorkerEventType.DEPARTED,
        WorkerEventType.ARRIVED,
        WorkerEventType.CANCELLED,
        WorkerEventType.NO_SHOW,
    }
)
_TERMINAL_EVENTS = frozenset(
    {
        WorkerEventType.REJECTED,
        WorkerEventType.COMPLETED,
        WorkerEventType.FAILED_TO_COMPLETE,
        WorkerEventType.CANCELLED,
        WorkerEventType.NO_SHOW,
    }
)


def _validate_snapshots(request: WorkerEventRequest) -> None:
    if request.appointment_id is None:
        raise InvalidWorkerEvent("appointment_required", event=request.event_type)
    if request.event_type in _PRE_WORK_EVENTS | {WorkerEventType.STARTED}:
        expected_ticket = TicketStatus.SCHEDULED
    else:
        expected_ticket = TicketStatus.IN_PROGRESS
    if request.ticket_status is not expected_ticket:
        raise InvalidWorkerEvent(
            "ticket_state_mismatch",
            event=request.event_type,
            current_status=request.ticket_status,
            expected_status=expected_ticket,
        )
    if request.appointment_status is not AppointmentStatus.BOOKED:
        raise InvalidWorkerEvent(
            "appointment_must_be_booked",
            event=request.event_type,
            current_status=request.appointment_status,
        )


def _validate_versions_and_purpose(request: WorkerEventRequest) -> None:
    ensure_expected_version(request.expected_ticket_version, request.actual_ticket_version)
    ensure_expected_version(
        request.expected_appointment_version, request.actual_appointment_version
    )
    if request.current_rework_count < 0:
        raise InvalidWorkerEvent(
            "invalid_rework_count", current_rework_count=request.current_rework_count
        )
    if request.event_type not in {WorkerEventType.REJECTED, WorkerEventType.CANCELLED}:
        return
    if request.appointment_purpose is None:
        raise InvalidWorkerEvent("appointment_purpose_required", event=request.event_type)
    expected_purpose = (
        AppointmentPurpose.REWORK
        if request.current_rework_count > 0
        else AppointmentPurpose.INITIAL_REPAIR
    )
    if request.appointment_purpose is not expected_purpose:
        raise InvalidWorkerEvent(
            "appointment_purpose_snapshot_conflict",
            event=request.event_type,
            appointment_purpose=request.appointment_purpose,
            current_rework_count=request.current_rework_count,
        )


def _validate_sequence(request: WorkerEventRequest) -> None:
    event = request.event_type
    prior = request.prior_events
    if any(item in _TERMINAL_EVENTS for item in prior):
        raise InvalidWorkerEvent("event_after_terminal_event", event=event, prior_events=prior)
    if event is WorkerEventType.ACCEPTED and prior:
        raise InvalidWorkerEvent("accepted_must_be_first", event=event, prior_events=prior)
    if event is WorkerEventType.REJECTED and prior:
        raise InvalidWorkerEvent("rejection_too_late", event=event, prior_events=prior)
    required_predecessor = {
        WorkerEventType.DEPARTED: WorkerEventType.ACCEPTED,
        WorkerEventType.ARRIVED: WorkerEventType.DEPARTED,
        WorkerEventType.COMPLETED: WorkerEventType.STARTED,
        WorkerEventType.FAILED_TO_COMPLETE: WorkerEventType.STARTED,
    }.get(event)
    if required_predecessor is not None and (not prior or prior[-1] is not required_predecessor):
        raise InvalidWorkerEvent(
            "missing_predecessor",
            event=event,
            required_event=required_predecessor,
            prior_events=prior,
        )
    if event in {WorkerEventType.CANCELLED, WorkerEventType.NO_SHOW}:
        if WorkerEventType.STARTED in prior:
            raise InvalidWorkerEvent("outcome_after_start", event=event, prior_events=prior)
    if event is WorkerEventType.CANCELLED and WorkerEventType.ACCEPTED not in prior:
        raise InvalidWorkerEvent(
            "cancellation_requires_acceptance", event=event, prior_events=prior
        )


def validate_worker_event(request: WorkerEventRequest) -> WorkerEventResult:
    """Validate an event and return its deterministic cross-entity effects."""

    _validate_versions_and_purpose(request)
    if request.actor_type not in _EVENT_ACTORS[request.event_type]:
        raise PermissionDenied(
            "actor_not_allowed", actor=request.actor_type, event=request.event_type
        )
    if request.confirmed_replay:
        if request.appointment_id is None:
            raise InvalidWorkerEvent("appointment_required", event=request.event_type)
        if request.event_type not in request.prior_events:
            raise InvalidWorkerEvent(
                "replay_event_not_recorded",
                event=request.event_type,
                prior_events=request.prior_events,
            )
        return WorkerEventResult(
            event_type=request.event_type,
            next_ticket_status=request.ticket_status,
            next_appointment_status=request.appointment_status,
            facts=(),
            requires_human_escalation=False,
            is_replay=True,
            next_rework_count=request.current_rework_count,
            cancellation_reason=request.cancellation_reason,
        )
    _validate_snapshots(request)
    _validate_sequence(request)

    ticket = request.ticket_status
    appointment = request.appointment_status
    requires_escalation = False
    failure_reason: FailureReason | None = None
    next_rework_count = request.current_rework_count
    event = request.event_type

    if event is WorkerEventType.REJECTED:
        if request.cancellation_reason is not CancellationReason.WORKER_REJECTED:
            raise InvalidWorkerEvent("worker_rejected_reason_required", event=event)
        ticket = (
            TicketStatus.REWORK_REQUIRED
            if request.appointment_purpose is AppointmentPurpose.REWORK
            else TicketStatus.OPEN
        )
        appointment = AppointmentStatus.CANCELLED
    elif event is WorkerEventType.STARTED:
        ticket = TicketStatus.IN_PROGRESS
    elif event is WorkerEventType.COMPLETED:
        ticket = TicketStatus.PENDING_ACCEPTANCE
        appointment = AppointmentStatus.FULFILLED
    elif event is WorkerEventType.FAILED_TO_COMPLETE:
        failure = request.failure
        if failure is None or not failure.worker_statement or not failure.evidence:
            raise InvalidWorkerEvent("complete_failure_details_required", event=event)
        disposition = failure_disposition(failure.reason)
        ticket = (
            TicketStatus.REWORK_REQUIRED
            if disposition is FailureDisposition.REWORK
            else TicketStatus.ESCALATED
        )
        appointment = AppointmentStatus.FULFILLED
        requires_escalation = disposition is FailureDisposition.ESCALATE
        failure_reason = failure.reason
        if disposition is FailureDisposition.REWORK:
            next_rework_count += 1
    elif event is WorkerEventType.CANCELLED:
        if request.cancellation_reason is not CancellationReason.WORKER_CANCELLED:
            raise InvalidWorkerEvent("worker_cancelled_reason_required", event=event)
        ticket = (
            TicketStatus.REWORK_REQUIRED
            if request.appointment_purpose is AppointmentPurpose.REWORK
            else TicketStatus.OPEN
        )
        appointment = AppointmentStatus.CANCELLED
    elif event is WorkerEventType.NO_SHOW:
        if request.no_show_reason is None or not request.evidence:
            raise InvalidWorkerEvent("reviewed_no_show_evidence_required", event=event)
        ticket = TicketStatus.ESCALATED
        appointment = AppointmentStatus.NO_SHOW
        requires_escalation = True

    return WorkerEventResult(
        event_type=event,
        next_ticket_status=ticket,
        next_appointment_status=appointment,
        facts=(DomainFact.WORKER_EVENT_OCCURRED, *ticket_follow_up_facts(ticket)),
        requires_human_escalation=requires_escalation,
        is_replay=False,
        next_rework_count=next_rework_count,
        cancellation_reason=request.cancellation_reason
        if event in {WorkerEventType.REJECTED, WorkerEventType.CANCELLED}
        else None,
        failure_reason=failure_reason,
    )

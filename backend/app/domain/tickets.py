"""Pure repair-ticket transition and escalation-recovery rules."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from app.domain.enums import (
    ESCALATABLE_TICKET_STATUSES,
    ActorType,
    AppointmentStatus,
    CancellationReason,
    DomainFact,
    EscalationDisposition,
    FailureDisposition,
    NoShowReason,
    TicketAction,
    TicketStatus,
    WorkerEventType,
    failure_disposition,
    ticket_follow_up_facts,
)
from app.domain.errors import (
    InvalidEscalationRecovery,
    InvalidTicketTransition,
    PermissionDenied,
)
from app.domain.invariants import ensure_expected_version
from app.domain.models import AcceptanceRejection, FailureDetails


@dataclass(frozen=True, slots=True)
class TicketTransitionRequest:
    """Complete input needed to evaluate one ticket transition."""

    current_status: TicketStatus
    action: TicketAction
    actor_type: ActorType
    expected_version: int
    actual_version: int
    resident_authorized: bool = True
    has_active_booked_appointment: bool = False
    cancellation_reason: CancellationReason | None = None
    no_show_reason: NoShowReason | None = None
    escalation_reason: str | None = None
    evidence: tuple[str, ...] = ()
    resident_acceptance: bool | None = None
    acceptance_rejection: AcceptanceRejection | None = None
    failure: FailureDetails | None = None
    rework_recorded: bool = False
    current_rework_count: int = 0


@dataclass(frozen=True, slots=True)
class TicketTransitionResult:
    """Side-effect-free decision returned to a future application service."""

    previous_status: TicketStatus
    next_status: TicketStatus
    next_version: int
    facts: tuple[DomainFact, ...]
    escalated_from_status: TicketStatus | None = None
    next_rework_count: int = 0


@dataclass(frozen=True, slots=True)
class TicketCreationRequest:
    """Preconditions for creating a new authorized ticket without a prior version."""

    actor_type: ActorType
    resident_authorized: bool
    issue_valid: bool
    duplicate_allowed: bool
    idempotency_accepted: bool


@dataclass(frozen=True, slots=True)
class TicketCreationPlan:
    """Pure result for the matrix's none-to-OPEN creation row."""

    initial_status: TicketStatus = TicketStatus.OPEN
    initial_version: int = 1
    initial_rework_count: int = 0
    facts: tuple[DomainFact, ...] = (DomainFact.TICKET_CREATED,)


@dataclass(frozen=True, slots=True)
class _TransitionSpec:
    next_status: TicketStatus
    allowed_actors: frozenset[ActorType]


_RESIDENT_OPERATOR = frozenset({ActorType.RESIDENT, ActorType.OPERATOR})
_WORKER_OPERATOR = frozenset({ActorType.WORKER, ActorType.OPERATOR})
_SYSTEM_OPERATOR = frozenset({ActorType.SYSTEM, ActorType.OPERATOR})
_ACTIONS_REQUIRING_BOOKING = frozenset(
    {
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
)
_ACTION_CANCELLATION_REASON: Mapping[TicketAction, CancellationReason] = MappingProxyType(
    {
        TicketAction.WORKER_REJECTED_INITIAL: CancellationReason.WORKER_REJECTED,
        TicketAction.WORKER_REJECTED_REWORK: CancellationReason.WORKER_REJECTED,
        TicketAction.WORKER_CANCELLED_INITIAL: CancellationReason.WORKER_CANCELLED,
        TicketAction.WORKER_CANCELLED_REWORK: CancellationReason.WORKER_CANCELLED,
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_INITIAL: (
            CancellationReason.RESIDENT_CANCELLED
        ),
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_REWORK: (CancellationReason.RESIDENT_CANCELLED),
    }
)
_INITIAL_APPOINTMENT_ACTIONS = frozenset(
    {
        TicketAction.WORKER_REJECTED_INITIAL,
        TicketAction.WORKER_CANCELLED_INITIAL,
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_INITIAL,
    }
)
_REWORK_APPOINTMENT_ACTIONS = frozenset(
    {
        TicketAction.WORKER_REJECTED_REWORK,
        TicketAction.WORKER_CANCELLED_REWORK,
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_REWORK,
    }
)

_transition_data: dict[tuple[TicketStatus, TicketAction], _TransitionSpec] = {
    (TicketStatus.OPEN, TicketAction.BOOK_APPOINTMENT): _TransitionSpec(
        TicketStatus.SCHEDULED, _RESIDENT_OPERATOR
    ),
    (TicketStatus.OPEN, TicketAction.CANCEL_TICKET): _TransitionSpec(
        TicketStatus.CANCELLED, _RESIDENT_OPERATOR
    ),
    (TicketStatus.SCHEDULED, TicketAction.START_WORK): _TransitionSpec(
        TicketStatus.IN_PROGRESS, _WORKER_OPERATOR
    ),
    (TicketStatus.SCHEDULED, TicketAction.RESCHEDULE): _TransitionSpec(
        TicketStatus.SCHEDULED, _RESIDENT_OPERATOR
    ),
    (TicketStatus.SCHEDULED, TicketAction.WORKER_REJECTED_INITIAL): _TransitionSpec(
        TicketStatus.OPEN, _WORKER_OPERATOR
    ),
    (TicketStatus.SCHEDULED, TicketAction.WORKER_REJECTED_REWORK): _TransitionSpec(
        TicketStatus.REWORK_REQUIRED, _WORKER_OPERATOR
    ),
    (TicketStatus.SCHEDULED, TicketAction.WORKER_CANCELLED_INITIAL): _TransitionSpec(
        TicketStatus.OPEN, _WORKER_OPERATOR
    ),
    (TicketStatus.SCHEDULED, TicketAction.WORKER_CANCELLED_REWORK): _TransitionSpec(
        TicketStatus.REWORK_REQUIRED, _WORKER_OPERATOR
    ),
    (
        TicketStatus.SCHEDULED,
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_INITIAL,
    ): _TransitionSpec(TicketStatus.OPEN, _RESIDENT_OPERATOR),
    (
        TicketStatus.SCHEDULED,
        TicketAction.RESIDENT_CANCELLED_APPOINTMENT_REWORK,
    ): _TransitionSpec(TicketStatus.REWORK_REQUIRED, _RESIDENT_OPERATOR),
    (TicketStatus.SCHEDULED, TicketAction.CANCEL_TICKET): _TransitionSpec(
        TicketStatus.CANCELLED, _RESIDENT_OPERATOR
    ),
    (TicketStatus.SCHEDULED, TicketAction.REVIEWED_NO_SHOW): _TransitionSpec(
        TicketStatus.ESCALATED, _SYSTEM_OPERATOR
    ),
    (TicketStatus.SCHEDULED, TicketAction.RESIDENT_ACCEPT): _TransitionSpec(
        TicketStatus.CLOSED, frozenset({ActorType.RESIDENT})
    ),
    (TicketStatus.IN_PROGRESS, TicketAction.RESIDENT_ACCEPT): _TransitionSpec(
        TicketStatus.CLOSED, frozenset({ActorType.RESIDENT})
    ),
    (TicketStatus.IN_PROGRESS, TicketAction.COMPLETE_WORK): _TransitionSpec(
        TicketStatus.PENDING_ACCEPTANCE, _WORKER_OPERATOR
    ),
    (TicketStatus.IN_PROGRESS, TicketAction.FAIL_WORK_REWORK): _TransitionSpec(
        TicketStatus.REWORK_REQUIRED, _WORKER_OPERATOR
    ),
    (TicketStatus.IN_PROGRESS, TicketAction.FAIL_WORK_ESCALATE): _TransitionSpec(
        TicketStatus.ESCALATED, _WORKER_OPERATOR
    ),
    (TicketStatus.PENDING_ACCEPTANCE, TicketAction.RESIDENT_ACCEPT): _TransitionSpec(
        TicketStatus.CLOSED, frozenset({ActorType.RESIDENT})
    ),
    (TicketStatus.PENDING_ACCEPTANCE, TicketAction.RESIDENT_REJECT): _TransitionSpec(
        TicketStatus.REWORK_REQUIRED, frozenset({ActorType.RESIDENT})
    ),
    (TicketStatus.REWORK_REQUIRED, TicketAction.BOOK_APPOINTMENT): _TransitionSpec(
        TicketStatus.SCHEDULED, _RESIDENT_OPERATOR
    ),
    (TicketStatus.REWORK_REQUIRED, TicketAction.CANCEL_TICKET): _TransitionSpec(
        TicketStatus.CANCELLED, frozenset({ActorType.OPERATOR})
    ),
}

for _status in ESCALATABLE_TICKET_STATUSES:
    _transition_data[(_status, TicketAction.ESCALATE)] = _TransitionSpec(
        TicketStatus.ESCALATED, _SYSTEM_OPERATOR
    )
_TRANSITIONS: Mapping[tuple[TicketStatus, TicketAction], _TransitionSpec] = MappingProxyType(
    _transition_data
)
del _transition_data


def plan_ticket_creation(request: TicketCreationRequest) -> TicketCreationPlan:
    """Validate creation preconditions and return the initial ticket facts."""

    if request.actor_type not in {ActorType.RESIDENT, ActorType.OPERATOR}:
        raise PermissionDenied("actor_not_allowed", actor=request.actor_type, action="CREATE")
    if request.actor_type is ActorType.RESIDENT and not request.resident_authorized:
        raise PermissionDenied("resident_not_authorized", actor=request.actor_type)
    if not request.issue_valid:
        raise InvalidTicketTransition("invalid_issue", action="CREATE")
    if not request.duplicate_allowed:
        raise InvalidTicketTransition("disallowed_duplicate", action="CREATE")
    if not request.idempotency_accepted:
        raise InvalidTicketTransition("idempotency_not_accepted", action="CREATE")
    return TicketCreationPlan()


def _check_resident_authorization(request: TicketTransitionRequest) -> None:
    if request.actor_type is ActorType.RESIDENT and not request.resident_authorized:
        raise PermissionDenied(
            "resident_not_authorized",
            actor=request.actor_type,
            current_status=request.current_status,
            action=request.action,
        )


def _check_booking_context(request: TicketTransitionRequest) -> None:
    if request.action in _ACTIONS_REQUIRING_BOOKING and not request.has_active_booked_appointment:
        raise InvalidTicketTransition(
            "active_booking_required",
            current_status=request.current_status,
            action=request.action,
        )
    if (
        request.action is TicketAction.BOOK_APPOINTMENT
        and request.current_status is TicketStatus.REWORK_REQUIRED
        and not request.rework_recorded
    ):
        raise InvalidTicketTransition(
            "rework_record_required",
            current_status=request.current_status,
            action=request.action,
        )
    if request.action in _INITIAL_APPOINTMENT_ACTIONS and request.current_rework_count != 0:
        raise InvalidTicketTransition(
            "initial_appointment_rework_count_conflict",
            action=request.action,
            current_rework_count=request.current_rework_count,
        )
    if request.action in _REWORK_APPOINTMENT_ACTIONS and request.current_rework_count < 1:
        raise InvalidTicketTransition(
            "rework_appointment_count_required",
            action=request.action,
            current_rework_count=request.current_rework_count,
        )
    if (
        request.action is TicketAction.BOOK_APPOINTMENT
        and request.current_status is TicketStatus.REWORK_REQUIRED
        and request.current_rework_count < 1
    ):
        raise InvalidTicketTransition(
            "rework_count_required",
            action=request.action,
            current_rework_count=request.current_rework_count,
        )


def _check_cancellation_context(request: TicketTransitionRequest) -> None:
    action = request.action
    if action is TicketAction.CANCEL_TICKET:
        if request.cancellation_reason is None:
            raise InvalidTicketTransition(
                "cancellation_reason_required", current_status=request.current_status, action=action
            )
        if request.current_status in {TicketStatus.OPEN, TicketStatus.REWORK_REQUIRED}:
            if request.has_active_booked_appointment:
                raise InvalidTicketTransition(
                    "active_booking_forbids_cancellation",
                    current_status=request.current_status,
                    action=action,
                )
        expected_reason = {
            ActorType.RESIDENT: CancellationReason.RESIDENT_CANCELLED,
            ActorType.OPERATOR: CancellationReason.OPERATOR_CANCELLED,
        }.get(request.actor_type)
        if request.cancellation_reason is not expected_reason:
            raise InvalidTicketTransition(
                "ticket_cancellation_reason_mismatch",
                current_status=request.current_status,
                action=action,
                actor=request.actor_type,
                reason=request.cancellation_reason,
            )
        if request.current_status is TicketStatus.SCHEDULED:
            if not request.has_active_booked_appointment:
                raise InvalidTicketTransition(
                    "scheduled_cancellation_requires_booking",
                    current_status=request.current_status,
                    action=action,
                )
    required_cancellation_reason = _ACTION_CANCELLATION_REASON.get(action)
    if required_cancellation_reason is not None:
        if request.cancellation_reason is not required_cancellation_reason:
            raise InvalidTicketTransition(
                "appointment_cancellation_reason_mismatch",
                current_status=request.current_status,
                action=action,
                reason=request.cancellation_reason,
            )


def _check_resolution_context(request: TicketTransitionRequest) -> None:
    action = request.action
    if action is TicketAction.REVIEWED_NO_SHOW and (
        request.no_show_reason is None or not request.evidence
    ):
        raise InvalidTicketTransition(
            "reviewed_no_show_context_required",
            current_status=request.current_status,
            action=action,
        )
    if action is TicketAction.ESCALATE and (not request.escalation_reason or not request.evidence):
        raise InvalidTicketTransition(
            "escalation_context_required", current_status=request.current_status, action=action
        )
    if action is TicketAction.RESIDENT_ACCEPT and request.resident_acceptance is not True:
        raise InvalidTicketTransition(
            "explicit_resident_acceptance_required",
            current_status=request.current_status,
            action=action,
        )
    if action is TicketAction.RESIDENT_REJECT and request.acceptance_rejection is None:
        raise InvalidTicketTransition(
            "acceptance_rejection_required", current_status=request.current_status, action=action
        )


def _check_failure_context(request: TicketTransitionRequest) -> None:
    action = request.action
    if action not in {TicketAction.FAIL_WORK_REWORK, TicketAction.FAIL_WORK_ESCALATE}:
        return
    if request.failure is None:
        raise InvalidTicketTransition(
            "failure_details_required", current_status=request.current_status, action=action
        )
    expected_disposition = (
        FailureDisposition.REWORK
        if action is TicketAction.FAIL_WORK_REWORK
        else FailureDisposition.ESCALATE
    )
    if failure_disposition(request.failure.reason) is not expected_disposition:
        raise InvalidTicketTransition(
            "failure_disposition_mismatch",
            current_status=request.current_status,
            action=action,
            reason=request.failure.reason,
        )
    if not request.failure.worker_statement or not request.failure.evidence:
        raise InvalidTicketTransition(
            "failure_evidence_required", current_status=request.current_status, action=action
        )


def _check_ticket_preconditions(request: TicketTransitionRequest) -> int:
    if request.current_rework_count < 0:
        raise InvalidTicketTransition(
            "invalid_rework_count", current_rework_count=request.current_rework_count
        )
    _check_resident_authorization(request)
    _check_booking_context(request)
    _check_cancellation_context(request)
    _check_resolution_context(request)
    _check_failure_context(request)
    should_increment = request.action in {
        TicketAction.RESIDENT_REJECT,
        TicketAction.FAIL_WORK_REWORK,
    }
    return request.current_rework_count + int(should_increment)


def _ticket_facts(next_status: TicketStatus) -> tuple[DomainFact, ...]:
    return (DomainFact.TICKET_TRANSITIONED, *ticket_follow_up_facts(next_status))


def transition_ticket(request: TicketTransitionRequest) -> TicketTransitionResult:
    """Apply the frozen ticket transition table without mutating state."""

    ensure_expected_version(request.expected_version, request.actual_version)
    spec = _TRANSITIONS.get((request.current_status, request.action))
    if spec is None:
        raise InvalidTicketTransition(
            "transition_not_allowed",
            current_status=request.current_status,
            action=request.action,
            actor=request.actor_type,
        )
    if request.actor_type not in spec.allowed_actors:
        raise PermissionDenied(
            "actor_not_allowed",
            current_status=request.current_status,
            action=request.action,
            target_status=spec.next_status,
            actor=request.actor_type,
        )
    next_rework_count = _check_ticket_preconditions(request)
    escalated_from = request.current_status if spec.next_status is TicketStatus.ESCALATED else None
    return TicketTransitionResult(
        previous_status=request.current_status,
        next_status=spec.next_status,
        next_version=request.actual_version + 1,
        facts=_ticket_facts(spec.next_status),
        escalated_from_status=escalated_from,
        next_rework_count=next_rework_count,
    )


@dataclass(frozen=True, slots=True)
class EscalationRecoveryRequest:
    """Latest domain snapshot used to derive, never request, a recovery target."""

    actor_type: ActorType
    expected_version: int
    actual_version: int
    escalated_from_status: TicketStatus | None
    appointment_status: AppointmentStatus | None
    latest_worker_event: WorkerEventType | None
    disposition: EscalationDisposition
    resident_acceptance: bool | None = None
    rework_recorded: bool = False
    conflict_resolved: bool = True
    current_rework_count: int = 0


def _derive_resume_target(request: EscalationRecoveryRequest) -> TicketStatus:
    prior = request.escalated_from_status
    appointment = request.appointment_status
    event = request.latest_worker_event
    if appointment is AppointmentStatus.BOOKED and event is WorkerEventType.STARTED:
        if prior is TicketStatus.IN_PROGRESS:
            return TicketStatus.IN_PROGRESS
    elif appointment is AppointmentStatus.BOOKED and prior in {
        TicketStatus.OPEN,
        TicketStatus.SCHEDULED,
        TicketStatus.REWORK_REQUIRED,
    }:
        return TicketStatus.SCHEDULED
    elif (
        appointment is AppointmentStatus.FULFILLED
        and event is WorkerEventType.COMPLETED
        and prior in {TicketStatus.IN_PROGRESS, TicketStatus.PENDING_ACCEPTANCE}
    ):
        return TicketStatus.PENDING_ACCEPTANCE
    elif prior in {
        TicketStatus.OPEN,
        TicketStatus.SCHEDULED,
    }:
        return TicketStatus.OPEN
    raise InvalidEscalationRecovery(
        "no_safe_resume_target",
        escalated_from_status=prior,
        appointment_status=appointment,
        latest_worker_event=event,
    )


def recover_escalated_ticket(request: EscalationRecoveryRequest) -> TicketTransitionResult:
    """Derive a safe target from reviewed current facts for an escalated ticket."""

    ensure_expected_version(request.expected_version, request.actual_version)
    if request.actor_type is not ActorType.OPERATOR:
        raise PermissionDenied("operator_required", actor=request.actor_type)
    if request.escalated_from_status not in ESCALATABLE_TICKET_STATUSES:
        raise InvalidEscalationRecovery(
            "invalid_escalated_from_status",
            escalated_from_status=request.escalated_from_status,
        )
    if not request.conflict_resolved:
        raise InvalidEscalationRecovery("unresolved_conflict")
    if request.current_rework_count < 0:
        raise InvalidEscalationRecovery(
            "invalid_rework_count", current_rework_count=request.current_rework_count
        )

    if request.disposition is EscalationDisposition.RESUME:
        target = _derive_resume_target(request)
    elif request.disposition is EscalationDisposition.APPROVE_REWORK:
        if not request.rework_recorded:
            raise InvalidEscalationRecovery("rework_record_required")
        target = TicketStatus.REWORK_REQUIRED
    elif request.disposition is EscalationDisposition.APPLY_RESIDENT_ACCEPTANCE:
        if (
            request.escalated_from_status is not TicketStatus.PENDING_ACCEPTANCE
            or request.resident_acceptance is not True
            or request.appointment_status is not AppointmentStatus.FULFILLED
            or request.latest_worker_event is not WorkerEventType.COMPLETED
        ):
            raise InvalidEscalationRecovery("resident_acceptance_evidence_required")
        target = TicketStatus.CLOSED
    else:
        if (
            request.appointment_status is AppointmentStatus.BOOKED
            or request.escalated_from_status
            not in {
                TicketStatus.OPEN,
                TicketStatus.SCHEDULED,
                TicketStatus.REWORK_REQUIRED,
            }
        ):
            raise InvalidEscalationRecovery("cancellation_not_safe")
        target = TicketStatus.CANCELLED

    return TicketTransitionResult(
        previous_status=TicketStatus.ESCALATED,
        next_status=target,
        next_version=request.actual_version + 1,
        facts=_ticket_facts(target),
        next_rework_count=request.current_rework_count
        + int(request.disposition is EscalationDisposition.APPROVE_REWORK),
    )

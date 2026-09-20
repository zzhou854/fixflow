"""Pure appointment transitions and atomic rescheduling plans."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from app.domain.enums import (
    TERMINAL_APPOINTMENT_STATUSES,
    ActorType,
    AppointmentAction,
    AppointmentPurpose,
    AppointmentStatus,
    CancellationReason,
    DomainFact,
    NoShowReason,
    TicketStatus,
)
from app.domain.errors import (
    InvalidAppointmentTransition,
    PermissionDenied,
)
from app.domain.invariants import ensure_expected_version
from app.domain.models import AppointmentDraft, AppointmentSnapshot, OutcomeMetadata


@dataclass(frozen=True, slots=True)
class AppointmentTransitionRequest:
    """Complete input for an existing appointment transition."""

    current_status: AppointmentStatus
    action: AppointmentAction
    actor_type: ActorType
    expected_version: int
    actual_version: int
    outcome: OutcomeMetadata | None = None


@dataclass(frozen=True, slots=True)
class AppointmentTransitionResult:
    """Pure appointment transition decision."""

    previous_status: AppointmentStatus
    next_status: AppointmentStatus
    next_version: int
    facts: tuple[DomainFact, ...] = (DomainFact.APPOINTMENT_TRANSITIONED,)


@dataclass(frozen=True, slots=True)
class AppointmentCreationPlan:
    """Validated creation of one formal BOOKED appointment and ticket effect."""

    new_appointment: AppointmentDraft
    next_ticket_status: TicketStatus
    next_ticket_version: int
    facts: tuple[DomainFact, ...] = (DomainFact.APPOINTMENT_CREATED,)


@dataclass(frozen=True, slots=True)
class ReschedulePlan:
    """Indivisible domain intent to supersede one booking and create another."""

    old_appointment_id: UUID
    old_transition: AppointmentTransitionResult
    new_appointment: AppointmentDraft
    facts: tuple[DomainFact, ...] = (DomainFact.APPOINTMENT_RESCHEDULED,)


_APPOINTMENT_TRANSITIONS: Mapping[
    AppointmentAction, tuple[AppointmentStatus, frozenset[ActorType]]
] = MappingProxyType(
    {
        AppointmentAction.SUPERSEDE: (
            AppointmentStatus.SUPERSEDED,
            frozenset({ActorType.RESIDENT, ActorType.OPERATOR}),
        ),
        AppointmentAction.CANCEL: (
            AppointmentStatus.CANCELLED,
            frozenset(ActorType),
        ),
        AppointmentAction.FULFILL: (
            AppointmentStatus.FULFILLED,
            frozenset({ActorType.RESIDENT, ActorType.WORKER, ActorType.OPERATOR}),
        ),
        AppointmentAction.MARK_NO_SHOW: (
            AppointmentStatus.NO_SHOW,
            frozenset({ActorType.OPERATOR, ActorType.SYSTEM}),
        ),
    }
)

_CANCELLATION_ACTORS: Mapping[CancellationReason, ActorType] = MappingProxyType(
    {
        CancellationReason.RESIDENT_CANCELLED: ActorType.RESIDENT,
        CancellationReason.WORKER_CANCELLED: ActorType.WORKER,
        CancellationReason.WORKER_REJECTED: ActorType.WORKER,
        CancellationReason.OPERATOR_CANCELLED: ActorType.OPERATOR,
        CancellationReason.SYSTEM_CANCELLED: ActorType.SYSTEM,
    }
)
_NO_SHOW_ACTORS: Mapping[NoShowReason, ActorType] = MappingProxyType(
    {
        NoShowReason.RESIDENT_NO_SHOW: ActorType.RESIDENT,
        NoShowReason.WORKER_NO_SHOW: ActorType.WORKER,
    }
)


def _validate_outcome(action: AppointmentAction, outcome: OutcomeMetadata | None) -> None:
    if action not in {AppointmentAction.CANCEL, AppointmentAction.MARK_NO_SHOW}:
        if outcome is not None:
            raise InvalidAppointmentTransition("outcome_not_allowed", action=action)
        return
    if outcome is None or not outcome.reason_text or not outcome.evidence:
        raise InvalidAppointmentTransition("complete_outcome_required", action=action)
    if action is AppointmentAction.CANCEL:
        if not isinstance(outcome.reason_code, CancellationReason):
            raise InvalidAppointmentTransition("cancellation_reason_required", action=action)
        expected_actor = _CANCELLATION_ACTORS[outcome.reason_code]
    else:
        if not isinstance(outcome.reason_code, NoShowReason):
            raise InvalidAppointmentTransition("no_show_reason_required", action=action)
        expected_actor = _NO_SHOW_ACTORS[outcome.reason_code]
    if outcome.actor_type is not expected_actor:
        raise InvalidAppointmentTransition(
            "outcome_actor_mismatch",
            action=action,
            actor=outcome.actor_type,
            reason=outcome.reason_code,
        )


def transition_appointment(
    request: AppointmentTransitionRequest,
) -> AppointmentTransitionResult:
    """Apply a frozen appointment transition without changing an entity."""

    ensure_expected_version(request.expected_version, request.actual_version)
    if request.current_status in TERMINAL_APPOINTMENT_STATUSES:
        raise InvalidAppointmentTransition(
            "terminal_appointment", current_status=request.current_status, action=request.action
        )
    next_status, allowed_actors = _APPOINTMENT_TRANSITIONS[request.action]
    if request.actor_type not in allowed_actors:
        raise PermissionDenied(
            "actor_not_allowed",
            current_status=request.current_status,
            action=request.action,
            target_status=next_status,
            actor=request.actor_type,
        )
    _validate_outcome(request.action, request.outcome)
    return AppointmentTransitionResult(
        previous_status=request.current_status,
        next_status=next_status,
        next_version=request.actual_version + 1,
    )


def plan_appointment_creation(
    draft: AppointmentDraft,
    *,
    current_ticket_status: TicketStatus,
    actor_type: ActorType,
    expected_ticket_version: int,
    actual_ticket_version: int,
) -> AppointmentCreationPlan:
    """Validate creation; candidate slots never call this function."""

    ensure_expected_version(expected_ticket_version, actual_ticket_version)
    if actor_type not in {ActorType.RESIDENT, ActorType.OPERATOR}:
        raise PermissionDenied("actor_not_allowed", actor=actor_type, action="BOOK")
    if current_ticket_status not in {TicketStatus.OPEN, TicketStatus.REWORK_REQUIRED}:
        raise InvalidAppointmentTransition(
            "ticket_not_bookable", current_status=current_ticket_status
        )
    if draft.status is not AppointmentStatus.BOOKED:
        raise InvalidAppointmentTransition("new_appointment_must_be_booked")
    expected_purpose = (
        AppointmentPurpose.REWORK
        if current_ticket_status is TicketStatus.REWORK_REQUIRED
        else AppointmentPurpose.INITIAL_REPAIR
    )
    if draft.purpose is not expected_purpose:
        raise InvalidAppointmentTransition(
            "appointment_purpose_ticket_conflict",
            appointment_purpose=draft.purpose,
            current_ticket_status=current_ticket_status,
        )
    if draft.supersedes_appointment_id is not None:
        raise InvalidAppointmentTransition("ordinary_booking_cannot_supersede")
    if draft.starts_at >= draft.ends_at:
        raise InvalidAppointmentTransition("invalid_appointment_interval")
    return AppointmentCreationPlan(
        new_appointment=draft,
        next_ticket_status=TicketStatus.SCHEDULED,
        next_ticket_version=actual_ticket_version + 1,
    )


def plan_reschedule(
    old: AppointmentSnapshot,
    replacement: AppointmentDraft,
    *,
    actor_type: ActorType,
    expected_version: int,
) -> ReschedulePlan:
    """Build an all-or-nothing replacement plan; no candidate mutates the old row."""

    transition = transition_appointment(
        AppointmentTransitionRequest(
            current_status=old.status,
            action=AppointmentAction.SUPERSEDE,
            actor_type=actor_type,
            expected_version=expected_version,
            actual_version=old.version,
        )
    )
    if replacement.status is not AppointmentStatus.BOOKED:
        raise InvalidAppointmentTransition("replacement_must_be_booked")
    if replacement.appointment_id == old.appointment_id:
        raise InvalidAppointmentTransition("replacement_requires_new_identity")
    if replacement.ticket_id != old.ticket_id:
        raise InvalidAppointmentTransition("replacement_ticket_mismatch")
    if replacement.purpose is not old.purpose:
        raise InvalidAppointmentTransition("reschedule_purpose_mismatch")
    if replacement.supersedes_appointment_id != old.appointment_id:
        raise InvalidAppointmentTransition("supersession_link_required")
    if replacement.starts_at >= replacement.ends_at:
        raise InvalidAppointmentTransition("invalid_replacement_interval")
    return ReschedulePlan(
        old_appointment_id=old.appointment_id,
        old_transition=transition,
        new_appointment=replacement,
    )

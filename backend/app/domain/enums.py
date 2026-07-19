"""Frozen domain enums and explicit state sets."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class IssueCategory(StrEnum):
    """Issue categories accepted for persisted release-1 repair tickets."""

    WATER_LEAK = "WATER_LEAK"
    ELECTRICAL = "ELECTRICAL"
    DOOR_LOCK = "DOOR_LOCK"


class Severity(StrEnum):
    """Persisted repair urgency; routing remains a domain-service decision."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    EMERGENCY = "EMERGENCY"


class WorkerSkillType(StrEnum):
    """Concrete maintenance capabilities used for deterministic matching."""

    PLUMBING = "PLUMBING"
    ELECTRICAL = "ELECTRICAL"
    LOCKSMITH = "LOCKSMITH"


ISSUE_CATEGORY_REQUIRED_SKILL: Mapping[IssueCategory, WorkerSkillType] = MappingProxyType(
    {
        IssueCategory.WATER_LEAK: WorkerSkillType.PLUMBING,
        IssueCategory.ELECTRICAL: WorkerSkillType.ELECTRICAL,
        IssueCategory.DOOR_LOCK: WorkerSkillType.LOCKSMITH,
    }
)


class TicketStatus(StrEnum):
    """Repair-ticket lifecycle state."""

    OPEN = "OPEN"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    PENDING_ACCEPTANCE = "PENDING_ACCEPTANCE"
    REWORK_REQUIRED = "REWORK_REQUIRED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class AppointmentStatus(StrEnum):
    """Formal appointment lifecycle state."""

    BOOKED = "BOOKED"
    FULFILLED = "FULFILLED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class AppointmentPurpose(StrEnum):
    """Business purpose distinguishing initial work from confirmed rework."""

    INITIAL_REPAIR = "INITIAL_REPAIR"
    REWORK = "REWORK"


class WorkerEventType(StrEnum):
    """Canonical maintenance-worker event type."""

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    DEPARTED = "DEPARTED"
    ARRIVED = "ARRIVED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED_TO_COMPLETE = "FAILED_TO_COMPLETE"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class WorkflowStage(StrEnum):
    """Recoverable orchestration progress, never domain truth."""

    INTAKE = "INTAKE"
    NEED_PROPERTY = "NEED_PROPERTY"
    NEED_INFO = "NEED_INFO"
    EMERGENCY_REVIEW = "EMERGENCY_REVIEW"
    POLICY_CHECK = "POLICY_CHECK"
    DUPLICATE_CHECK = "DUPLICATE_CHECK"
    EXISTING_TICKET = "EXISTING_TICKET"
    CREATING_TICKET = "CREATING_TICKET"
    UNKNOWN_COMMIT = "UNKNOWN_COMMIT"
    FINDING_SLOTS = "FINDING_SLOTS"
    AWAITING_SLOT_CONFIRMATION = "AWAITING_SLOT_CONFIRMATION"
    BOOKING = "BOOKING"
    MONITORING_APPOINTMENT = "MONITORING_APPOINTMENT"
    RESCHEDULING = "RESCHEDULING"
    STATUS_CONFLICT = "STATUS_CONFLICT"
    AWAITING_ACCEPTANCE = "AWAITING_ACCEPTANCE"
    PLANNING_REWORK = "PLANNING_REWORK"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    DONE = "DONE"


class ActorType(StrEnum):
    """Domain actor category."""

    RESIDENT = "RESIDENT"
    OPERATOR = "OPERATOR"
    WORKER = "WORKER"
    SYSTEM = "SYSTEM"


class CancellationReason(StrEnum):
    """Typed reason accompanying appointment cancellation."""

    RESIDENT_CANCELLED = "RESIDENT_CANCELLED"
    WORKER_CANCELLED = "WORKER_CANCELLED"
    WORKER_REJECTED = "WORKER_REJECTED"
    OPERATOR_CANCELLED = "OPERATOR_CANCELLED"
    SYSTEM_CANCELLED = "SYSTEM_CANCELLED"


class NoShowReason(StrEnum):
    """Typed subject of an adjudicated no-show."""

    RESIDENT_NO_SHOW = "RESIDENT_NO_SHOW"
    WORKER_NO_SHOW = "WORKER_NO_SHOW"


class FailureReason(StrEnum):
    """Typed reason for a worker failing to complete a repair attempt."""

    REPAIR_INCOMPLETE = "REPAIR_INCOMPLETE"
    FOLLOW_UP_REQUIRED = "FOLLOW_UP_REQUIRED"
    SAFETY_RISK = "SAFETY_RISK"
    RESPONSIBILITY_CONFLICT = "RESPONSIBILITY_CONFLICT"
    SPECIAL_RESOURCE_REQUIRED = "SPECIAL_RESOURCE_REQUIRED"
    INDETERMINATE = "INDETERMINATE"


class FailureDisposition(StrEnum):
    """Deterministic routing for a failed repair attempt."""

    REWORK = "REWORK"
    ESCALATE = "ESCALATE"


class AcceptanceRejectionReason(StrEnum):
    """Typed resident reason for rejecting repair acceptance."""

    ISSUE_NOT_RESOLVED = "ISSUE_NOT_RESOLVED"
    PROBLEM_RECURRED = "PROBLEM_RECURRED"
    WORK_QUALITY_CONCERN = "WORK_QUALITY_CONCERN"
    OTHER = "OTHER"


class TicketAction(StrEnum):
    """Explicit ticket command names used by the transition table."""

    BOOK_APPOINTMENT = "BOOK_APPOINTMENT"
    START_WORK = "START_WORK"
    RESCHEDULE = "RESCHEDULE"
    WORKER_REJECTED_INITIAL = "WORKER_REJECTED_INITIAL"
    WORKER_REJECTED_REWORK = "WORKER_REJECTED_REWORK"
    WORKER_CANCELLED_INITIAL = "WORKER_CANCELLED_INITIAL"
    WORKER_CANCELLED_REWORK = "WORKER_CANCELLED_REWORK"
    RESIDENT_CANCELLED_APPOINTMENT_INITIAL = "RESIDENT_CANCELLED_APPOINTMENT_INITIAL"
    RESIDENT_CANCELLED_APPOINTMENT_REWORK = "RESIDENT_CANCELLED_APPOINTMENT_REWORK"
    REVIEWED_NO_SHOW = "REVIEWED_NO_SHOW"
    COMPLETE_WORK = "COMPLETE_WORK"
    FAIL_WORK_REWORK = "FAIL_WORK_REWORK"
    FAIL_WORK_ESCALATE = "FAIL_WORK_ESCALATE"
    RESIDENT_ACCEPT = "RESIDENT_ACCEPT"
    RESIDENT_REJECT = "RESIDENT_REJECT"
    CANCEL_TICKET = "CANCEL_TICKET"
    ESCALATE = "ESCALATE"


class AppointmentAction(StrEnum):
    """Explicit appointment command names."""

    SUPERSEDE = "SUPERSEDE"
    CANCEL = "CANCEL"
    FULFILL = "FULFILL"
    MARK_NO_SHOW = "MARK_NO_SHOW"


class EscalationDisposition(StrEnum):
    """Reviewed operator disposition; it is not a requested target state."""

    RESUME = "RESUME"
    APPROVE_REWORK = "APPROVE_REWORK"
    APPLY_RESIDENT_ACCEPTANCE = "APPLY_RESIDENT_ACCEPTANCE"
    CANCEL = "CANCEL"


class DomainFact(StrEnum):
    """Typed business facts returned for application-layer handling."""

    TICKET_CREATED = "TICKET_CREATED"
    TICKET_TRANSITIONED = "TICKET_TRANSITIONED"
    APPOINTMENT_CREATED = "APPOINTMENT_CREATED"
    APPOINTMENT_TRANSITIONED = "APPOINTMENT_TRANSITIONED"
    WORKER_EVENT_OCCURRED = "WORKER_EVENT_OCCURRED"
    RESIDENT_ACCEPTANCE_REQUIRED = "RESIDENT_ACCEPTANCE_REQUIRED"
    REWORK_REQUIRED = "REWORK_REQUIRED"
    HUMAN_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"
    APPOINTMENT_RESCHEDULED = "APPOINTMENT_RESCHEDULED"
    REWORK_APPOINTMENT_PLANNED = "REWORK_APPOINTMENT_PLANNED"


TERMINAL_TICKET_STATUSES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.CANCELLED, TicketStatus.CLOSED}
)
TERMINAL_APPOINTMENT_STATUSES: frozenset[AppointmentStatus] = frozenset(
    {
        AppointmentStatus.FULFILLED,
        AppointmentStatus.SUPERSEDED,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.NO_SHOW,
    }
)
ESCALATABLE_TICKET_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.OPEN,
        TicketStatus.SCHEDULED,
        TicketStatus.IN_PROGRESS,
        TicketStatus.PENDING_ACCEPTANCE,
        TicketStatus.REWORK_REQUIRED,
    }
)

REWORK_FAILURE_REASONS: frozenset[FailureReason] = frozenset(
    {FailureReason.REPAIR_INCOMPLETE, FailureReason.FOLLOW_UP_REQUIRED}
)
ESCALATION_FAILURE_REASONS: frozenset[FailureReason] = frozenset(
    {
        FailureReason.SAFETY_RISK,
        FailureReason.RESPONSIBILITY_CONFLICT,
        FailureReason.SPECIAL_RESOURCE_REQUIRED,
        FailureReason.INDETERMINATE,
    }
)


def failure_disposition(reason: FailureReason) -> FailureDisposition:
    """Return the frozen deterministic routing for a failure reason."""

    if reason in REWORK_FAILURE_REASONS:
        return FailureDisposition.REWORK
    return FailureDisposition.ESCALATE


def ticket_follow_up_facts(status: TicketStatus) -> tuple[DomainFact, ...]:
    """Return business follow-up facts implied by a ticket's new status."""

    if status is TicketStatus.PENDING_ACCEPTANCE:
        return (DomainFact.RESIDENT_ACCEPTANCE_REQUIRED,)
    if status is TicketStatus.REWORK_REQUIRED:
        return (DomainFact.REWORK_REQUIRED,)
    if status is TicketStatus.ESCALATED:
        return (DomainFact.HUMAN_REVIEW_REQUIRED,)
    return ()

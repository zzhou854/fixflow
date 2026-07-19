"""Typed commands and results at the deterministic application boundary."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.domain.enums import (
    AcceptanceRejectionReason,
    ActorType,
    CancellationReason,
    EscalationDisposition,
    FailureReason,
    IssueCategory,
    NoShowReason,
    Severity,
    WorkerEventType,
)


@dataclass(frozen=True, slots=True)
class MutationMetadata:
    """Identity, audit, concurrency, and retry metadata for one mutation."""

    actor_type: ActorType
    actor_id: UUID
    trace_id: UUID
    idempotency_key: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CreateTicketCommand:
    metadata: MutationMetadata
    resident_id: UUID
    property_id: UUID
    issue_category: IssueCategory
    issue_location: str
    issue_description: str
    severity: Severity
    allow_duplicate: bool = False


@dataclass(frozen=True, slots=True)
class BookAppointmentCommand:
    metadata: MutationMetadata
    ticket_id: UUID
    worker_id: UUID
    starts_at: datetime
    ends_at: datetime
    expected_ticket_version: int


@dataclass(frozen=True, slots=True)
class RescheduleAppointmentCommand:
    metadata: MutationMetadata
    ticket_id: UUID
    appointment_id: UUID
    worker_id: UUID
    starts_at: datetime
    ends_at: datetime
    expected_ticket_version: int
    expected_appointment_version: int


@dataclass(frozen=True, slots=True)
class RecordWorkerEventCommand:
    metadata: MutationMetadata
    ticket_id: UUID
    appointment_id: UUID
    subject_worker_id: UUID
    event_type: WorkerEventType
    external_event_key: str
    expected_ticket_version: int
    expected_appointment_version: int
    cancellation_reason: CancellationReason | None = None
    no_show_reason: NoShowReason | None = None
    reason_text: str | None = None
    evidence: tuple[str, ...] = ()
    failure_reason: FailureReason | None = None
    worker_statement: str | None = None


@dataclass(frozen=True, slots=True)
class ReviewRepairCommand:
    metadata: MutationMetadata
    ticket_id: UUID
    expected_ticket_version: int
    accepted: bool
    rejection_reason: AcceptanceRejectionReason | None = None
    explanation: str | None = None


@dataclass(frozen=True, slots=True)
class EscalateTicketCommand:
    metadata: MutationMetadata
    ticket_id: UUID
    expected_ticket_version: int
    reason_code: str
    reason_text: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecoverTicketCommand:
    metadata: MutationMetadata
    ticket_id: UUID
    expected_ticket_version: int
    disposition: EscalationDisposition
    conflict_resolved: bool
    resident_acceptance: bool | None = None
    rework_recorded: bool = False
    reason_text: str | None = None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class OperationResult:
    """Transport-independent result that a future MCP adapter can map directly."""

    ok: bool
    code: str
    resource_type: str | None = None
    resource_id: UUID | None = None
    resource_version: int | None = None
    replayed: bool = False
    data: dict[str, Any] = field(default_factory=dict)

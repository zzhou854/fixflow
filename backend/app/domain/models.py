"""Immutable values shared by the pure domain rules."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.enums import (
    AcceptanceRejectionReason,
    ActorType,
    AppointmentPurpose,
    AppointmentStatus,
    CancellationReason,
    FailureReason,
    NoShowReason,
)


@dataclass(frozen=True, slots=True)
class OutcomeMetadata:
    """Typed, immutable metadata for cancellation or no-show outcomes."""

    actor_type: ActorType
    actor_id: UUID
    reason_code: CancellationReason | NoShowReason
    reason_text: str
    evidence: tuple[str, ...]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class FailureDetails:
    """Required evidence for a failed repair attempt."""

    reason: FailureReason
    worker_statement: str
    evidence: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptanceRejection:
    """Resident's typed rejection of completed repair work."""

    reason: AcceptanceRejectionReason
    explanation: str | None = None


@dataclass(frozen=True, slots=True)
class AppointmentSnapshot:
    """Infrastructure-free appointment facts used by domain validation."""

    appointment_id: UUID
    ticket_id: UUID
    worker_id: UUID
    purpose: AppointmentPurpose
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus
    version: int
    supersedes_appointment_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AppointmentDraft:
    """New appointment facts to be persisted atomically by a future service."""

    appointment_id: UUID
    ticket_id: UUID
    worker_id: UUID
    purpose: AppointmentPurpose
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus = AppointmentStatus.BOOKED
    supersedes_appointment_id: UUID | None = None

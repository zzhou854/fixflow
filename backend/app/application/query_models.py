"""Typed commands and read models for deterministic application queries."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.enums import (
    ActorType,
    AppointmentPurpose,
    AppointmentStatus,
    IssueCategory,
    Severity,
    TicketStatus,
    WorkerEventType,
    WorkerSkillType,
)


@dataclass(frozen=True, slots=True)
class QueryActor:
    actor_type: ActorType
    actor_id: UUID


@dataclass(frozen=True, slots=True)
class GetResidentPropertyQuery:
    actor: QueryActor
    resident_id: UUID
    property_id: UUID


@dataclass(frozen=True, slots=True)
class FindOpenRepairTicketsQuery:
    actor: QueryActor
    resident_id: UUID
    property_id: UUID
    issue_category: IssueCategory
    normalized_issue_location: str


@dataclass(frozen=True, slots=True)
class GetTicketSnapshotQuery:
    actor: QueryActor
    ticket_id: UUID


@dataclass(frozen=True, slots=True)
class ListAvailableSlotsQuery:
    actor: QueryActor
    property_id: UUID
    issue_category: IssueCategory
    search_window_start: datetime
    search_window_end: datetime
    requested_duration_minutes: int
    max_results: int


@dataclass(frozen=True, slots=True)
class ResidentPropertyReadModel:
    resident_id: UUID
    property_id: UUID
    community_name: str
    building_no: str
    unit_no: str
    room_no: str
    address_text: str


@dataclass(frozen=True, slots=True)
class OpenRepairTicketReadModel:
    ticket_id: UUID
    ticket_version: int
    resident_id: UUID
    property_id: UUID
    issue_category: IssueCategory
    issue_location: str
    severity: Severity
    ticket_status: TicketStatus
    rework_count: int


@dataclass(frozen=True, slots=True)
class AppointmentReadModel:
    appointment_id: UUID
    worker_id: UUID
    purpose: AppointmentPurpose
    status: AppointmentStatus
    scheduled_start: datetime
    scheduled_end: datetime
    appointment_version: int


@dataclass(frozen=True, slots=True)
class WorkerEventReadModel:
    event_id: UUID
    event_type: WorkerEventType
    sequence_no: int
    subject_worker_id: UUID


@dataclass(frozen=True, slots=True)
class TicketSnapshotReadModel:
    ticket_id: UUID
    ticket_version: int
    resident_id: UUID
    property_id: UUID
    issue_category: IssueCategory
    issue_location: str
    severity: Severity
    ticket_status: TicketStatus
    rework_count: int
    escalated_from_status: TicketStatus | None
    active_appointment: AppointmentReadModel | None
    latest_worker_event: WorkerEventReadModel | None


@dataclass(frozen=True, slots=True)
class TimeWindow:
    starts_at: datetime
    ends_at: datetime
    lower_inclusive: bool = True


@dataclass(frozen=True, slots=True)
class SlotWorkerSource:
    worker_id: UUID
    worker_name: str
    skill_type: WorkerSkillType
    service_area: str
    open_ticket_count: int
    availability: tuple[TimeWindow, ...]
    booked_intervals: tuple[TimeWindow, ...]


@dataclass(frozen=True, slots=True)
class AvailableSlotReadModel:
    worker_id: UUID
    worker_name: str
    skill_type: WorkerSkillType
    service_area: str
    service_area_matched: bool
    scheduled_start: datetime
    scheduled_end: datetime
    open_ticket_count: int
    rank: int
    slot_granularity_minutes: int

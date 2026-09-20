from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiModel
from app.domain.enums import (
    ActorType,
    AppointmentPurpose,
    AppointmentStatus,
    FailureReason,
    IssueCategory,
    Severity,
    TicketStatus,
    WorkerEventType,
)
from app.infrastructure.database.models.reconciliation import (
    ReconciliationAction,
    ReconciliationStatus,
)


class PropertyResponse(ApiModel):
    property_id: UUID
    community_name: str
    building_no: str
    unit_no: str
    room_no: str
    address_text: str


class AppointmentResponse(ApiModel):
    appointment_id: UUID
    worker_id: UUID
    purpose: AppointmentPurpose
    status: AppointmentStatus
    scheduled_start: datetime
    scheduled_end: datetime
    appointment_version: int


class AvailableSlotResponse(ApiModel):
    worker_id: UUID
    worker_name: str
    scheduled_start: datetime
    scheduled_end: datetime
    rank: int


class AvailableSlotPageResponse(ApiModel):
    items: tuple[AvailableSlotResponse, ...]


class OperatorBookAppointmentRequest(ApiModel):
    worker_id: UUID
    scheduled_start: datetime
    scheduled_end: datetime
    expected_ticket_version: int = Field(ge=1)


class TicketListItemResponse(ApiModel):
    ticket_id: UUID
    resident_id: UUID
    resident_username: str
    property_id: UUID
    property_label: str
    issue_category: IssueCategory
    issue_location: str
    severity: Severity
    ticket_status: TicketStatus
    rework_count: int
    version: int
    appointment: AppointmentResponse | None
    updated_at: datetime


class TicketHistoryResponse(ApiModel):
    from_status: TicketStatus | None
    to_status: TicketStatus
    action: str
    actor_type: ActorType
    reason_code: str | None
    reason_text: str | None
    version_after: int
    created_at: datetime


class AppointmentHistoryResponse(ApiModel):
    from_status: AppointmentStatus | None
    to_status: AppointmentStatus
    actor_type: ActorType
    reason_code: str | None
    reason_text: str | None
    version_after: int
    created_at: datetime


class WorkerEventResponse(ApiModel):
    event_id: UUID
    event_type: WorkerEventType
    sequence_no: int
    subject_worker_id: UUID


class TicketDetailResponse(ApiModel):
    ticket: TicketListItemResponse
    issue_description: str
    escalated_from_status: TicketStatus | None
    ticket_history: tuple[TicketHistoryResponse, ...]
    appointment_history: tuple[AppointmentHistoryResponse, ...]
    latest_worker_event: WorkerEventResponse | None


class TicketPageResponse(ApiModel):
    items: tuple[TicketListItemResponse, ...]
    limit: int
    offset: int


class EscalateTicketRequest(ApiModel):
    expected_version: int = Field(ge=1)
    reason_code: str = Field(min_length=1, max_length=80)
    reason_text: str = Field(min_length=1, max_length=1000)
    evidence: tuple[str, ...] = Field(default=(), max_length=20)


class RecordRepairProgressRequest(ApiModel):
    appointment_id: UUID
    worker_id: UUID
    expected_ticket_version: int = Field(ge=1)
    expected_appointment_version: int = Field(ge=1)
    event_type: WorkerEventType
    failure_reason: FailureReason | None = None
    note: str | None = Field(default=None, max_length=1000)


class AcceptRepairRequest(ApiModel):
    expected_ticket_version: int = Field(ge=1)
    expected_appointment_version: int | None = Field(default=None, ge=1)


class OperationResponse(ApiModel):
    ok: bool
    code: str
    resource_type: str | None
    resource_id: UUID | None
    resource_version: int | None
    replayed: bool
    reconciliation: "ReconciliationPendingResponse | None" = None


class ReconciliationPendingResponse(ApiModel):
    case_id: UUID
    action: ReconciliationAction
    status: ReconciliationStatus
    retry_allowed: bool
    ticket_id: UUID

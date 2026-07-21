"""Ticket query and mutation MCP contracts."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.domain.enums import IssueCategory, Severity, TicketStatus, WorkerEventType
from app.property_operations.contracts.common import MCPContractModel, MutationRequest, ReadRequest


class FindOpenRepairTicketsRequest(ReadRequest):
    resident_id: UUID
    property_id: UUID
    issue_category: IssueCategory
    normalized_issue_location: str = Field(min_length=1, max_length=255)


class CreateRepairTicketRequest(MutationRequest):
    resident_id: UUID
    property_id: UUID
    issue_category: IssueCategory
    issue_location: str = Field(min_length=1, max_length=255)
    issue_description: str = Field(min_length=1, max_length=4000)
    severity: Severity
    allow_duplicate: bool = False


class GetTicketSnapshotRequest(ReadRequest):
    ticket_id: UUID


class EscalationReasonCode(StrEnum):
    MANUAL_REVIEW = "MANUAL_REVIEW"
    SAFETY_RISK = "SAFETY_RISK"
    RESPONSIBILITY_CONFLICT = "RESPONSIBILITY_CONFLICT"
    SPECIAL_RESOURCE_REQUIRED = "SPECIAL_RESOURCE_REQUIRED"
    INDETERMINATE = "INDETERMINATE"
    SCHEDULING_UNAVAILABLE = "SCHEDULING_UNAVAILABLE"


class EscalateToOperatorRequest(MutationRequest):
    ticket_id: UUID
    expected_version: int = Field(ge=1)
    reason_code: EscalationReasonCode
    reason_text: str = Field(min_length=1, max_length=1000)
    evidence: tuple[str, ...] = Field(default=(), max_length=20)


class OpenRepairTicketItem(MCPContractModel):
    ticket_id: UUID
    ticket_version: int
    resident_id: UUID
    property_id: UUID
    issue_category: IssueCategory
    issue_location: str
    severity: Severity
    ticket_status: TicketStatus
    rework_count: int


class OpenRepairTicketsData(MCPContractModel):
    match_type: str = "EXACT_STRUCTURED_CANDIDATE"
    tickets: tuple[OpenRepairTicketItem, ...]


class ActiveAppointmentData(MCPContractModel):
    appointment_id: UUID
    worker_id: UUID
    purpose: str
    appointment_status: str
    scheduled_start: datetime
    scheduled_end: datetime
    appointment_version: int


class LatestWorkerEventData(MCPContractModel):
    event_id: UUID
    event_type: WorkerEventType
    sequence_no: int
    subject_worker_id: UUID


class TicketSnapshotData(MCPContractModel):
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
    active_appointment: ActiveAppointmentData | None
    latest_worker_event: LatestWorkerEventData | None

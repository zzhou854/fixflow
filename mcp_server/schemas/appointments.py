"""Candidate-slot, booking, and rescheduling MCP contracts."""

from datetime import datetime
from uuid import UUID

from app.domain.enums import IssueCategory, WorkerSkillType
from pydantic import Field, model_validator

from mcp_server.schemas.common import MCPContractModel, MutationRequest, ReadRequest


def _ensure_aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


class ListAvailableSlotsRequest(ReadRequest):
    property_id: UUID
    issue_category: IssueCategory
    search_window_start: datetime
    search_window_end: datetime
    requested_duration_minutes: int = Field(gt=0, le=1440)
    max_results: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_window(self) -> "ListAvailableSlotsRequest":
        _ensure_aware(self.search_window_start, "search_window_start")
        _ensure_aware(self.search_window_end, "search_window_end")
        if self.search_window_end <= self.search_window_start:
            raise ValueError("search_window_end must be after search_window_start")
        return self


class BookAppointmentRequest(MutationRequest):
    ticket_id: UUID
    worker_id: UUID
    scheduled_start: datetime
    scheduled_end: datetime
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_interval(self) -> "BookAppointmentRequest":
        _ensure_aware(self.scheduled_start, "scheduled_start")
        _ensure_aware(self.scheduled_end, "scheduled_end")
        if self.scheduled_end <= self.scheduled_start:
            raise ValueError("scheduled_end must be after scheduled_start")
        return self


class RescheduleAppointmentRequest(MutationRequest):
    ticket_id: UUID
    appointment_id: UUID
    worker_id: UUID
    scheduled_start: datetime
    scheduled_end: datetime
    expected_version: int = Field(ge=1)
    expected_appointment_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_interval(self) -> "RescheduleAppointmentRequest":
        _ensure_aware(self.scheduled_start, "scheduled_start")
        _ensure_aware(self.scheduled_end, "scheduled_end")
        if self.scheduled_end <= self.scheduled_start:
            raise ValueError("scheduled_end must be after scheduled_start")
        return self


class AvailableSlotItem(MCPContractModel):
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


class AvailableSlotsData(MCPContractModel):
    slot_granularity_minutes: int = 30
    booking_guaranteed: bool = False
    slots: tuple[AvailableSlotItem, ...]

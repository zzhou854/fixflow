"""Sanitized operator contracts for pre-ticket human review."""

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.api.schemas.common import ApiModel
from app.application.agent_reliability_models import (
    HumanReviewFailureStage,
    HumanReviewSafetyLevel,
    HumanReviewStatus,
)


class HumanReviewCaseResponse(ApiModel):
    case_id: UUID
    thread_id: UUID
    resident_id: UUID
    property_id: UUID | None
    ticket_id: UUID | None
    source_run_id: UUID | None
    intent_version: int
    failure_stage: HumanReviewFailureStage
    reason_code: str
    last_error_code: str | None
    safety_level: HumanReviewSafetyLevel
    priority: int
    summary: str
    status: HumanReviewStatus
    assigned_operator_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    claimed_at: datetime | None
    resolved_at: datetime | None
    resolution_code: str | None
    resolution_note: str | None
    resident_username: str | None
    property_address: str | None


class HumanReviewCasePage(ApiModel):
    items: tuple[HumanReviewCaseResponse, ...]
    limit: int
    offset: int


class HumanReviewTransitionRequest(ApiModel):
    target_status: HumanReviewStatus
    expected_version: int = Field(ge=1)
    resolution_code: str | None = Field(default=None, max_length=80)
    resolution_note: str | None = Field(default=None, max_length=2000)


class HumanReviewEventResponse(ApiModel):
    event_id: UUID
    case_id: UUID
    sequence_no: int
    from_status: HumanReviewStatus | None
    to_status: HumanReviewStatus
    action: str
    actor_type: str
    actor_id: UUID
    trace_id: UUID
    version_before: int
    version_after: int
    reason_code: str | None
    note: str | None
    occurred_at: datetime


class HumanReviewEventPage(ApiModel):
    items: tuple[HumanReviewEventResponse, ...]

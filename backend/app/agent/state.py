"""Strict serializable Agent work state; PostgreSQL remains business truth."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from app.agent.enums import AgentIntent, IssueField, PendingAction, SafetyFlag
from app.agent.models import (
    AgentModel,
    DescriptionText,
    LocationText,
    TimeWindow,
    normalize_issue_location,
)
from app.domain.enums import ActorType, IssueCategory, Severity, WorkflowStage
from app.policy.enums import EvidenceSufficiency, PolicyTopic


class ToolResultSummary(AgentModel):
    result_code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1000)
    resource_id: UUID | None = None
    resource_version: int | None = Field(default=None, ge=1)
    retryable: bool = False


class CandidateSlot(AgentModel):
    worker_id: UUID
    scheduled_start: datetime
    scheduled_end: datetime
    rank: int = Field(ge=1)
    booking_guaranteed: Literal[False] = False

    @model_validator(mode="after")
    def validate_interval(self) -> "CandidateSlot":
        if self.scheduled_start.tzinfo is None or self.scheduled_start.utcoffset() is None:
            raise ValueError("scheduled_start must be timezone-aware")
        if self.scheduled_end.tzinfo is None or self.scheduled_end.utcoffset() is None:
            raise ValueError("scheduled_end must be timezone-aware")
        if self.scheduled_end <= self.scheduled_start:
            raise ValueError("scheduled_end must be after scheduled_start")
        return self


def compute_missing_fields(
    *,
    task_intent: AgentIntent,
    property_id: UUID | None,
    issue_category: IssueCategory | None,
    normalized_issue_location: str | None,
    issue_description: str | None,
    user_availability_windows: tuple[TimeWindow, ...],
) -> tuple[IssueField, ...]:
    """Return the minimal deterministic field matrix for the active task goal."""

    missing: list[IssueField] = []
    if task_intent is AgentIntent.NEW_REPAIR:
        if property_id is None:
            missing.append(IssueField.PROPERTY)
        if issue_category is None:
            missing.append(IssueField.ISSUE_CATEGORY)
        if normalized_issue_location is None:
            missing.append(IssueField.ISSUE_LOCATION)
        if issue_description is None:
            missing.append(IssueField.ISSUE_DESCRIPTION)
    elif task_intent is AgentIntent.RESCHEDULE_APPOINTMENT and not user_availability_windows:
        missing.append(IssueField.AVAILABILITY)
    return tuple(missing)


class AgentState(AgentModel):
    """Conversation work state; never an authoritative ticket snapshot."""

    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)

    thread_id: UUID
    trace_id: UUID
    actor_type: ActorType
    actor_id: UUID
    user_id: UUID
    property_id: UUID | None = None
    active_ticket_id: UUID | None = None
    active_appointment_id: UUID | None = None
    task_intent: AgentIntent = AgentIntent.UNKNOWN
    utterance_intent: AgentIntent = AgentIntent.UNKNOWN
    intent_version: int = Field(default=1, ge=1)
    issue_category: IssueCategory | None = None
    issue_location: LocationText | None = None
    normalized_issue_location: LocationText | None = None
    issue_description: DescriptionText | None = None
    severity: Severity | None = None
    safety_flags: tuple[SafetyFlag, ...] = Field(default=(), max_length=10)
    user_availability_windows: tuple[TimeWindow, ...] = Field(default=(), max_length=20)
    candidate_slots: tuple[CandidateSlot, ...] = Field(default=(), max_length=100)
    missing_fields: tuple[IssueField, ...] = Field(default=(), max_length=12)
    policy_evidence_ids: tuple[UUID, ...] = Field(default=(), max_length=50)
    policy_conflict: bool = False
    policy_sufficiency: EvidenceSufficiency | None = None
    missing_policy_topics: tuple[PolicyTopic, ...] = Field(default=(), max_length=9)
    policy_retrieved_as_of: datetime | None = None
    policy_query_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    safety_review_required: bool = False
    snapshot_refresh_required: bool = False
    workflow_stage: WorkflowStage = WorkflowStage.INTAKE
    pending_action: PendingAction = PendingAction.NONE
    user_confirmation: bool | None = None
    ticket_snapshot_version: int | None = Field(default=None, ge=1)
    appointment_version: int | None = Field(default=None, ge=1)
    last_tool_result: ToolResultSummary | None = None
    retry_count: int = Field(default=0, ge=0, le=20)
    escalation_reason: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_normalized_location(self) -> "AgentState":
        expected = normalize_issue_location(self.issue_location)
        if self.normalized_issue_location != expected:
            raise ValueError("normalized_issue_location must match issue_location")
        expected_missing = compute_missing_fields(
            task_intent=self.task_intent,
            property_id=self.property_id,
            issue_category=self.issue_category,
            normalized_issue_location=self.normalized_issue_location,
            issue_description=self.issue_description,
            user_availability_windows=self.user_availability_windows,
        )
        if self.missing_fields != expected_missing:
            raise ValueError("missing_fields must match deterministic state requirements")
        return self

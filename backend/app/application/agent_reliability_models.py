"""Typed application models for durable Agent results and human review."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.domain.enums import ActorType


class MessageOutcome(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"


class RequiredUserAction(StrEnum):
    NONE = "NONE"
    PROVIDE_DETAILS = "PROVIDE_DETAILS"
    SELECT_SLOT = "SELECT_SLOT"
    CONFIRM_ACTION = "CONFIRM_ACTION"
    CONTACT_OPERATOR = "CONTACT_OPERATOR"
    RETRY = "RETRY"


class ThreadPropertyResolutionStatus(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    RESOLVED = "RESOLVED"
    DENIED = "DENIED"


class ThreadLifecycleStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class AgentMessageRole(StrEnum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"


class HumanReviewStatus(StrEnum):
    OPEN = "OPEN"
    CLAIMED = "CLAIMED"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class HumanReviewFailureStage(StrEnum):
    INTERPRETATION = "INTERPRETATION"
    PROPERTY_AUTHORIZATION = "PROPERTY_AUTHORIZATION"
    SAFETY_REVIEW = "SAFETY_REVIEW"
    POLICY_REVIEW = "POLICY_REVIEW"
    DUPLICATE_CHECK = "DUPLICATE_CHECK"
    SCHEDULING = "SCHEDULING"
    MUTATION_RECONCILIATION = "MUTATION_RECONCILIATION"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"


class HumanReviewSafetyLevel(StrEnum):
    STANDARD = "STANDARD"
    ELEVATED = "ELEVATED"
    EMERGENCY = "EMERGENCY"


@dataclass(frozen=True, slots=True)
class DurableMessage:
    message_id: UUID
    thread_id: UUID
    run_id: UUID
    role: AgentMessageRole
    content: str
    sequence_no: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ThreadRecord:
    thread_id: UUID
    resident_id: UUID
    property_id: UUID | None
    property_resolution_status: ThreadPropertyResolutionStatus
    lifecycle_status: ThreadLifecycleStatus
    display_title: str | None
    last_activity_at: datetime
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    version: int


@dataclass(frozen=True, slots=True)
class HumanReviewCaseRecord:
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
    dedupe_key: str
    version: int
    created_at: datetime
    updated_at: datetime
    claimed_at: datetime | None
    resolved_at: datetime | None
    resolution_code: str | None
    resolution_note: str | None


@dataclass(frozen=True, slots=True)
class PublicRunResult:
    run_id: UUID
    message_outcome: MessageOutcome
    required_user_action: RequiredUserAction


@dataclass(frozen=True, slots=True)
class FinalizeAgentRun:
    run_id: UUID
    thread_id: UUID
    trace_id: UUID
    resident_id: UUID
    property_id: UUID | None
    intent_version: int
    user_message_id: UUID | None
    user_message: str | None
    user_message_created_at: datetime | None
    assistant_message: str
    outcome: MessageOutcome
    required_user_action: RequiredUserAction
    agent_run_status: str
    agent_run_terminal_event_type: str
    message_event_type: str
    error_code: str | None
    failure_stage: HumanReviewFailureStage | None = None
    reason_code: str | None = None
    safety_level: HumanReviewSafetyLevel = HumanReviewSafetyLevel.STANDARD
    active_ticket_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class HumanReviewTransition:
    case_id: UUID
    actor_type: ActorType
    actor_id: UUID
    trace_id: UUID
    expected_version: int
    target_status: HumanReviewStatus
    resolution_code: str | None = None
    resolution_note: str | None = None


@dataclass(frozen=True, slots=True)
class HumanReviewEventRecord:
    event_id: UUID
    case_id: UUID
    sequence_no: int
    from_status: HumanReviewStatus | None
    to_status: HumanReviewStatus
    action: str
    actor_type: ActorType
    actor_id: UUID
    trace_id: UUID
    version_before: int
    version_after: int
    reason_code: str | None
    note: str | None
    occurred_at: datetime

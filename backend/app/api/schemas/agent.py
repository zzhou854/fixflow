from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.agent.enums import AgentIntent, AgentReconciliationStatus, IssueField, PendingAction
from app.agent_runtime.models import RunStatus
from app.api.schemas.common import ApiModel
from app.api.schemas.tickets import AppointmentResponse, TicketListItemResponse
from app.application.agent_reliability_models import (
    MessageOutcome,
    RequiredUserAction,
    ThreadLifecycleStatus,
)
from app.domain.enums import IssueCategory, Severity, WorkflowStage
from app.policy.enums import EvidenceSufficiency


class CreateThreadRequest(ApiModel):
    property_id: UUID | None = None
    initial_message: str = Field(min_length=1, max_length=4000)
    timezone_name: str = Field(min_length=1, max_length=64)
    reference_time: datetime


class SendMessageRequest(ApiModel):
    message: str = Field(min_length=1, max_length=4000)
    message_id: UUID | None = None
    timezone_name: str = Field(min_length=1, max_length=64)
    reference_time: datetime


class ProvideInformationResumeRequest(ApiModel):
    kind: Literal["PROVIDE_INFORMATION"]
    intent_version: int = Field(ge=1)
    user_message: str = Field(min_length=1, max_length=4000)
    reference_time: datetime
    timezone_name: str = Field(min_length=1, max_length=64)


class SelectDuplicateTicketResumeRequest(ApiModel):
    kind: Literal["SELECT_DUPLICATE_TICKET"]
    intent_version: int = Field(ge=1)
    candidates_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ticket_id: UUID


class SelectAppointmentSlotResumeRequest(ApiModel):
    kind: Literal["SELECT_APPOINTMENT_SLOT"]
    intent_version: int = Field(ge=1)
    candidates_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    rank: int = Field(ge=1)


ResumeRequest = Annotated[
    ProvideInformationResumeRequest
    | SelectDuplicateTicketResumeRequest
    | SelectAppointmentSlotResumeRequest,
    Field(discriminator="kind"),
]


class NeedInformationInterruptResponse(ApiModel):
    kind: Literal["NEED_INFORMATION"]
    intent_version: int
    missing_fields: tuple[str, ...]
    message: str


class DuplicateTicketResponse(ApiModel):
    ticket_id: UUID
    ticket_version: int
    ticket_status: str
    issue_location: str


class DuplicateTicketInterruptResponse(ApiModel):
    kind: Literal["DUPLICATE_TICKET_SELECTION"]
    intent_version: int
    candidates_fingerprint: str
    tickets: tuple[DuplicateTicketResponse, ...]


class SlotResponse(ApiModel):
    rank: int
    worker_id: UUID
    scheduled_start: datetime
    scheduled_end: datetime
    booking_guaranteed: Literal[False]


class SlotInterruptResponse(ApiModel):
    kind: Literal["APPOINTMENT_SLOT_SELECTION"]
    intent_version: int
    candidates_fingerprint: str
    slots: tuple[SlotResponse, ...]


InterruptResponse = Annotated[
    NeedInformationInterruptResponse | DuplicateTicketInterruptResponse | SlotInterruptResponse,
    Field(discriminator="kind"),
]


class PolicyStatusResponse(ApiModel):
    sufficiency: EvidenceSufficiency | None
    conflict: bool
    evidence_ids: tuple[UUID, ...]


class StructuredIssueResponse(ApiModel):
    issue_category: IssueCategory | None
    issue_location: str | None
    issue_description: str | None
    severity: Severity | None


class ResidentConversationMessageResponse(ApiModel):
    role: Literal["USER", "ASSISTANT"]
    content: str
    created_at: datetime


class ResidentThreadSummaryResponse(ApiModel):
    thread_id: UUID
    property_id: UUID | None
    workflow_stage: WorkflowStage
    run_status: RunStatus
    lifecycle_status: ThreadLifecycleStatus
    issue_category: IssueCategory | None
    issue_location: str | None
    active_ticket_id: UUID | None
    updated_at: datetime
    archived_at: datetime | None
    version: int


class ResidentThreadListResponse(ApiModel):
    items: tuple[ResidentThreadSummaryResponse, ...]
    limit: int
    offset: int


class AgentThreadResponse(ApiModel):
    thread_id: UUID
    trace_id: UUID
    run_id: UUID | None = None
    message_id: UUID | None = None
    workflow_stage: WorkflowStage
    run_status: RunStatus
    message_outcome: MessageOutcome = MessageOutcome.COMPLETED
    required_user_action: RequiredUserAction = RequiredUserAction.NONE
    business_status: str = "REQUEST_COMPLETED"
    template_id: str = "GENERIC_UPDATE"
    display_action_text: str | None = None
    assistant_message: str | None
    interrupt: InterruptResponse | None
    active_ticket: TicketListItemResponse | None
    active_appointment: AppointmentResponse | None
    policy_status: PolicyStatusResponse
    structured_issue: StructuredIssueResponse
    safety_review_required: bool
    error_code: str | None = None
    development_mode: Literal[True] = True
    reconciliation: "ResidentReconciliationResponse | None" = None
    conversation_messages: tuple[ResidentConversationMessageResponse, ...] = ()


class ThreadLifecycleResponse(ApiModel):
    thread_id: UUID
    lifecycle_status: ThreadLifecycleStatus
    archived_at: datetime | None
    version: int


class ThreadLifecycleRequest(ApiModel):
    expected_version: int = Field(ge=1)


class ResidentReconciliationResponse(ApiModel):
    case_id: UUID
    status: AgentReconciliationStatus
    action: PendingAction
    retry_allowed: bool


class OperatorThreadResponse(ApiModel):
    thread_id: UUID
    workflow_stage: WorkflowStage
    run_status: RunStatus
    task_intent: AgentIntent
    issue_category: IssueCategory | None
    issue_location: str | None
    severity: Severity | None
    policy_sufficiency: EvidenceSufficiency | None
    policy_conflict: bool
    policy_evidence_summary: tuple[UUID, ...]
    missing_fields: tuple[IssueField, ...]
    active_ticket_id: UUID | None
    active_appointment_id: UUID | None
    human_review_required: bool
    updated_at: datetime | None


class SSEEvent(ApiModel):
    event_id: UUID
    event_type: Literal[
        "run_started",
        "assistant_delta",
        "message.completed",
        "message.failed",
        "message.escalated",
        "workflow_updated",
        "run_failed",
        "heartbeat",
    ]
    thread_id: UUID
    trace_id: UUID
    run_id: UUID | None = None
    sequence: int = 0
    timestamp: datetime
    data: dict[str, object]

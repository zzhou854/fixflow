"""Strict public inputs, resumes, interrupts, and sanitized runtime results."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, StringConstraints, TypeAdapter, model_validator

from app.agent.enums import AgentIntent, AgentReconciliationStatus, IssueField, PendingAction
from app.agent.models import AgentModel
from app.domain.enums import ActorType, IssueCategory, Severity, WorkflowStage
from app.policy.enums import EvidenceSufficiency


class RunStatus(StrEnum):
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"
    FAILED_SAFE = "FAILED_SAFE"


class InterruptKind(StrEnum):
    NEED_INFORMATION = "NEED_INFORMATION"
    DUPLICATE_TICKET_SELECTION = "DUPLICATE_TICKET_SELECTION"
    APPOINTMENT_SLOT_SELECTION = "APPOINTMENT_SLOT_SELECTION"


class AgentTurnInput(AgentModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: UUID
    trace_id: UUID
    actor_type: ActorType
    actor_id: UUID
    user_id: UUID
    property_id: UUID | None = None
    user_message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    reference_time: datetime
    timezone_name: Annotated[str, StringConstraints(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def validate_time(self) -> "AgentTurnInput":
        from app.agent.models import InterpretMessageInput

        # Reuse the frozen IANA timezone and offset contract.
        InterpretMessageInput(
            current_user_message=self.user_message,
            recent_conversation_messages=(),
            current_state_summary={"intent_version": 1},
            current_workflow_stage=WorkflowStage.INTAKE,
            known_issue_fields={},
            missing_fields=(),
            reference_time=self.reference_time,
            timezone_name=self.timezone_name,
        )
        return self


class AgentCallerContext(AgentModel):
    """Identity supplied by a future trusted authentication boundary.

    It intentionally excludes ``trace_id`` and mutable request data.  The
    Orchestrator compares it with the immutable identity frozen in the first
    successfully authorised checkpoint for a thread.
    """

    model_config = ConfigDict(extra="forbid")

    actor_type: ActorType
    actor_id: UUID
    user_id: UUID


class ThreadOwnerIdentity(AgentModel):
    """The immutable owner tuple stored across the lifetime of one thread."""

    actor_type: ActorType
    actor_id: UUID
    user_id: UUID
    property_id: UUID


class ProvideInformationResume(AgentModel):
    kind: Literal["PROVIDE_INFORMATION"]
    intent_version: int = Field(ge=1)
    user_message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
    ]
    trace_id: UUID
    reference_time: datetime
    timezone_name: Annotated[str, StringConstraints(min_length=1, max_length=64)]


class SelectDuplicateTicketResume(AgentModel):
    kind: Literal["SELECT_DUPLICATE_TICKET"]
    intent_version: int = Field(ge=1)
    candidates_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    ticket_id: UUID
    trace_id: UUID


class SelectAppointmentSlotResume(AgentModel):
    kind: Literal["SELECT_APPOINTMENT_SLOT"]
    intent_version: int = Field(ge=1)
    candidates_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    rank: int = Field(ge=1)
    trace_id: UUID


AgentResume = Annotated[
    ProvideInformationResume | SelectDuplicateTicketResume | SelectAppointmentSlotResume,
    Field(discriminator="kind"),
]
AGENT_RESUME_ADAPTER: TypeAdapter[AgentResume] = TypeAdapter(AgentResume)


class NeedInformationInterrupt(AgentModel):
    kind: Literal[InterruptKind.NEED_INFORMATION] = InterruptKind.NEED_INFORMATION
    intent_version: int
    missing_fields: tuple[str, ...]
    message: str


class DuplicateTicketItem(AgentModel):
    ticket_id: UUID
    ticket_version: int
    ticket_status: str
    issue_location: str


class DuplicateTicketSelectionInterrupt(AgentModel):
    kind: Literal[InterruptKind.DUPLICATE_TICKET_SELECTION] = (
        InterruptKind.DUPLICATE_TICKET_SELECTION
    )
    intent_version: int
    candidates_fingerprint: str
    tickets: tuple[DuplicateTicketItem, ...]


class SlotItem(AgentModel):
    rank: int
    worker_id: UUID
    scheduled_start: datetime
    scheduled_end: datetime
    booking_guaranteed: Literal[False] = False


class AppointmentSlotSelectionInterrupt(AgentModel):
    kind: Literal[InterruptKind.APPOINTMENT_SLOT_SELECTION] = (
        InterruptKind.APPOINTMENT_SLOT_SELECTION
    )
    intent_version: int
    candidates_fingerprint: str
    slots: tuple[SlotItem, ...]


InterruptPayload = Annotated[
    NeedInformationInterrupt
    | DuplicateTicketSelectionInterrupt
    | AppointmentSlotSelectionInterrupt,
    Field(discriminator="kind"),
]
INTERRUPT_ADAPTER: TypeAdapter[InterruptPayload] = TypeAdapter(InterruptPayload)


class AgentStateView(AgentModel):
    thread_id: UUID
    intent_version: int
    workflow_stage: WorkflowStage
    property_id: UUID | None
    property_context_verified: bool
    active_ticket_id: UUID | None
    active_appointment_id: UUID | None
    ticket_snapshot_version: int | None
    appointment_version: int | None
    last_assistant_message: str | None
    run_status: RunStatus
    interrupt: InterruptPayload | None = None
    issue_category: IssueCategory | None = None
    issue_location: str | None = None
    issue_description: str | None = None
    severity: Severity | None = None
    policy_evidence_ids: tuple[UUID, ...] = ()
    policy_conflict: bool = False
    policy_sufficiency: EvidenceSufficiency | None = None
    safety_review_required: bool = False
    task_intent: AgentIntent = AgentIntent.UNKNOWN
    missing_fields: tuple[IssueField, ...] = ()
    updated_at: datetime | None = None
    pending_reconciliation_case_id: UUID | None = None
    pending_reconciliation_status: AgentReconciliationStatus | None = None
    pending_reconciliation_action: PendingAction | None = None


class AgentRunResult(AgentModel):
    thread_id: UUID
    trace_id: UUID
    run_status: RunStatus
    assistant_message: str | None = None
    interrupt: InterruptPayload | None = None
    workflow_stage: WorkflowStage
    active_ticket_id: UUID | None = None
    active_appointment_id: UUID | None = None
    error_code: str | None = None
    pending_reconciliation_case_id: UUID | None = None
    pending_reconciliation_status: AgentReconciliationStatus | None = None
    pending_reconciliation_action: PendingAction | None = None

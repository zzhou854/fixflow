"""Strict replay-safe inputs, state projections, and comparison results."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

from app.agent.enums import (
    AgentIntent,
    AgentReconciliationStatus,
    IssueField,
    LLMRole,
    PendingAction,
    SafetyFlag,
)
from app.agent.models import TimeWindow
from app.agent.state import (
    CachedTicketSnapshot,
    CandidateSlot,
    DuplicateTicketCandidate,
    ToolResultSummary,
)
from app.domain.enums import ActorType, IssueCategory, Severity, WorkflowStage
from app.infrastructure.database.models.observability import AgentRunTrigger
from app.policy.enums import EvidenceSufficiency, PolicyTopic
from app.replay.enums import (
    RecoveryRecommendation,
    ReplayBundleStatus,
    ReplayExecutionStatus,
    ReplayMismatchType,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ReplayModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplayMessageMetadata(ReplayModel):
    message_id: UUID
    content_hash: Sha256
    content_length: int = Field(ge=1, le=4000)
    language: str = Field(min_length=2, max_length=16)
    message_role: LLMRole


class ThreadCreatedReplayInput(ReplayModel):
    kind: Literal["THREAD_CREATED"] = "THREAD_CREATED"
    property_id: UUID
    message: ReplayMessageMetadata
    reference_time: datetime
    timezone_name: str = Field(min_length=1, max_length=64)


class MessageReplayInput(ReplayModel):
    kind: Literal["MESSAGE"] = "MESSAGE"
    message: ReplayMessageMetadata
    reference_time: datetime
    timezone_name: str = Field(min_length=1, max_length=64)


class ProvideInformationReplayInput(ReplayModel):
    kind: Literal["PROVIDE_INFORMATION"] = "PROVIDE_INFORMATION"
    intent_version: int = Field(ge=1)
    message: ReplayMessageMetadata
    reference_time: datetime
    timezone_name: str = Field(min_length=1, max_length=64)


class SelectDuplicateReplayInput(ReplayModel):
    kind: Literal["SELECT_DUPLICATE_TICKET"] = "SELECT_DUPLICATE_TICKET"
    intent_version: int = Field(ge=1)
    candidate_fingerprint: Sha256
    ticket_id: UUID


class SelectSlotReplayInput(ReplayModel):
    kind: Literal["SELECT_APPOINTMENT_SLOT"] = "SELECT_APPOINTMENT_SLOT"
    intent_version: int = Field(ge=1)
    candidate_fingerprint: Sha256
    rank: int = Field(ge=1)


ResumeReplayInput = Annotated[
    ProvideInformationReplayInput | SelectDuplicateReplayInput | SelectSlotReplayInput,
    Field(discriminator="kind"),
]


class OperatorActionReplayInput(ReplayModel):
    kind: Literal["OPERATOR_ACTION"] = "OPERATOR_ACTION"
    action: Literal["ESCALATE_TO_OPERATOR"]
    target_entity_id: UUID
    expected_version: int = Field(ge=1)
    request_fingerprint: Sha256


ReplayInputEnvelope = Annotated[
    ThreadCreatedReplayInput
    | MessageReplayInput
    | ProvideInformationReplayInput
    | SelectDuplicateReplayInput
    | SelectSlotReplayInput
    | OperatorActionReplayInput,
    Field(discriminator="kind"),
]
REPLAY_INPUT_ADAPTER: TypeAdapter[ReplayInputEnvelope] = TypeAdapter(ReplayInputEnvelope)


class ThreadOwnerProjection(ReplayModel):
    actor_type: ActorType
    actor_id: UUID
    user_id: UUID
    property_id: UUID


class PendingOperationProjection(ReplayModel):
    operation_id: UUID | None = None
    action: PendingAction
    request_fingerprint: Sha256
    target_entity_type: str | None = Field(default=None, max_length=40)
    target_entity_id: UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)
    normalized_payload_fingerprint: Sha256


class ReconciliationProjection(ReplayModel):
    case_id: UUID | None = None
    status: AgentReconciliationStatus | None = None
    action: PendingAction | None = None


class ReplaySafeAgentState(ReplayModel):
    thread_id: UUID | None
    trace_id: UUID
    actor_type: ActorType
    actor_id: UUID
    user_id: UUID
    thread_owner_identity: ThreadOwnerProjection | None = None
    property_id: UUID | None = None
    property_context_verified: bool
    workflow_stage: WorkflowStage
    run_status: str | None = Field(default=None, max_length=40)
    task_intent: AgentIntent
    utterance_intent: AgentIntent
    intent_version: int = Field(ge=1)
    issue_category: IssueCategory | None = None
    issue_location: str | None = Field(default=None, max_length=255)
    normalized_issue_location: str | None = Field(default=None, max_length=255)
    safe_issue_summary: str | None = Field(default=None, max_length=1000)
    severity: Severity | None = None
    safety_flags: tuple[SafetyFlag, ...] = Field(default=(), max_length=10)
    safety_review_required: bool
    missing_fields: tuple[IssueField, ...] = Field(default=(), max_length=12)
    policy_sufficiency: EvidenceSufficiency | None = None
    policy_conflict: bool
    policy_evidence_ids: tuple[UUID, ...] = Field(default=(), max_length=50)
    missing_policy_topics: tuple[PolicyTopic, ...] = Field(default=(), max_length=9)
    active_ticket_id: UUID | None = None
    active_appointment_id: UUID | None = None
    active_ticket_snapshot: CachedTicketSnapshot | None = None
    ticket_snapshot_version: int | None = Field(default=None, ge=1)
    appointment_version: int | None = Field(default=None, ge=1)
    duplicate_candidate_summaries: tuple[DuplicateTicketCandidate, ...] = Field(
        default=(), max_length=20
    )
    duplicate_candidates_fingerprint: Sha256 | None = None
    slot_candidate_summaries: tuple[CandidateSlot, ...] = Field(default=(), max_length=100)
    slot_candidates_fingerprint: Sha256 | None = None
    selected_candidate_slot: CandidateSlot | None = None
    user_availability_windows: tuple[TimeWindow, ...] = Field(default=(), max_length=20)
    pending_action: PendingAction
    last_tool_result: ToolResultSummary | None = None
    pending_operation_projection: PendingOperationProjection | None = None
    reconciliation_projection: ReconciliationProjection | None = None
    reference_time: datetime | None = None
    timezone_name: str | None = Field(default=None, max_length=64)
    service_duration_minutes: int | None = Field(default=None, gt=0, le=1440)
    conversation_metadata: tuple[ReplayMessageMetadata, ...] = Field(default=(), max_length=100)


class ReplayMismatch(ReplayModel):
    mismatch_type: ReplayMismatchType
    step_key: str | None = Field(default=None, max_length=160)
    expected_summary: str | None = Field(default=None, max_length=500)
    actual_summary: str | None = Field(default=None, max_length=500)


class ReplayComparisonResult(ReplayModel):
    status: ReplayExecutionStatus
    node_path: tuple[str, ...]
    route_fingerprint: Sha256 | None = None
    state_fingerprint: Sha256 | None = None
    mismatches: tuple[ReplayMismatch, ...] = Field(default=(), max_length=200)
    consumed_steps: int = Field(ge=0)
    total_steps: int = Field(ge=0)


class ReplayBundleView(ReplayModel):
    bundle_id: UUID
    original_run_id: UUID
    thread_id: UUID | None
    original_trace_id: UUID
    trigger_type: AgentRunTrigger
    status: ReplayBundleStatus
    schema_version: int = Field(ge=1)
    graph_schema_version: int = Field(ge=1)
    runtime_revision: str
    start_state: ReplaySafeAgentState | None
    input_envelope: ReplayInputEnvelope
    expected_result: ReplaySafeAgentState | None
    expected_route_fingerprint: Sha256 | None
    expected_state_fingerprint: Sha256 | None
    step_count: int = Field(ge=0)
    bundle_checksum: Sha256 | None
    capture_error_code: str | None = None
    captured_at: datetime
    finalized_at: datetime | None


class ReplayExecutionView(ReplayModel):
    execution_id: UUID
    bundle_id: UUID
    requested_by_actor_id: UUID
    requested_by_user_id: UUID
    status: ReplayExecutionStatus
    runtime_revision: str
    graph_schema_version: int
    started_at: datetime
    completed_at: datetime | None
    actual_route_fingerprint: Sha256 | None = None
    actual_state_fingerprint: Sha256 | None = None
    mismatches: tuple[ReplayMismatch, ...] = ()
    error_code: str | None = None
    recommendation: RecoveryRecommendation | None = None

"""Strict contracts passed to and returned from the language-only nodes."""

from datetime import datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.agent.enums import (
    AcceptanceDecision,
    AgentIntent,
    IssueField,
    LLMRole,
    SafetyFlag,
)
from app.domain.enums import IssueCategory, Severity, WorkflowStage

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
LocationText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
]
DescriptionText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)
]


class AgentModel(BaseModel):
    # Closed schemas remain JSON-friendly at provider boundaries: JSON UUIDs,
    # enums, timestamps, arrays, and objects must still parse into typed fields.
    model_config = ConfigDict(extra="forbid")


def _aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def normalize_issue_location(value: str | None) -> str | None:
    """Normalize only deterministic formatting differences for intent comparison."""

    if value is None:
        return None
    normalized = " ".join(value.strip().casefold().split())
    return normalized or None


class TimeWindow(AgentModel):
    starts_at: datetime
    ends_at: datetime

    @model_validator(mode="after")
    def validate_range(self) -> "TimeWindow":
        _aware(self.starts_at, "starts_at")
        _aware(self.ends_at, "ends_at")
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class ConversationMessage(AgentModel):
    role: LLMRole
    content: Annotated[str, StringConstraints(min_length=1, max_length=4000)]


class KnownIssueFields(AgentModel):
    issue_category: IssueCategory | None = None
    issue_location: LocationText | None = None
    normalized_issue_location: LocationText | None = None
    issue_description: DescriptionText | None = None
    severity: Severity | None = None


class AgentStateSummary(AgentModel):
    active_ticket_id: UUID | None = None
    active_appointment_id: UUID | None = None
    task_intent: AgentIntent = AgentIntent.UNKNOWN
    utterance_intent: AgentIntent = AgentIntent.UNKNOWN
    intent_version: int = Field(ge=1)


class InterpretMessageInput(AgentModel):
    current_user_message: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    recent_conversation_messages: tuple[ConversationMessage, ...] = Field(max_length=12)
    current_state_summary: AgentStateSummary
    current_workflow_stage: WorkflowStage
    known_issue_fields: KnownIssueFields
    missing_fields: tuple[IssueField, ...] = Field(max_length=12)
    reference_time: datetime
    timezone_name: Annotated[str, StringConstraints(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def validate_time_context(self) -> "InterpretMessageInput":
        _aware(self.reference_time, "reference_time")
        try:
            timezone = ZoneInfo(self.timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone_name must be a valid IANA timezone") from exc
        expected_offset = self.reference_time.astimezone(timezone).utcoffset()
        if self.reference_time.utcoffset() != expected_offset:
            raise ValueError("reference_time offset must match timezone_name")
        return self


class InterpretMessageOutput(AgentModel):
    utterance_intent: AgentIntent
    issue_category: IssueCategory | None = None
    issue_location: LocationText | None = None
    issue_description_update: DescriptionText | None = None
    safety_flags: tuple[SafetyFlag, ...] = Field(default=(), max_length=10)
    user_availability_windows: tuple[TimeWindow, ...] = Field(default=(), max_length=20)
    user_correction: bool = False
    acceptance_decision: AcceptanceDecision | None = None
    requested_human: bool = False
    explicit_property_reference: UUID | None = None
    model_suggested_missing_fields: tuple[IssueField, ...] = Field(default=(), max_length=12)


class LLMMessage(AgentModel):
    role: LLMRole
    content: Annotated[str, StringConstraints(min_length=1, max_length=12000)]


class LLMRequestConfig(AgentModel):
    model: ShortText
    prompt_name: ShortText
    prompt_version: ShortText
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1000, ge=1, le=8000)


class StructuredLLMResult(AgentModel):
    payload: dict[str, object]
    provider: ShortText
    model: ShortText
    prompt_name: ShortText
    prompt_version: ShortText
    prompt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    schema_version: str | None = Field(default=None, max_length=100)
    request_id: str | None = Field(default=None, max_length=200)
    finish_reason: str | None = Field(default=None, max_length=100)
    latency_ms: int | None = Field(default=None, ge=0)
    attempt_count: int = Field(default=1, ge=1, le=5)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    thinking_mode: str | None = Field(default=None, max_length=20)
    transport_tool_call_count: int = Field(default=0, ge=0)
    json_decoded: bool = True
    schema_validated: bool = True
    invariants_validated: bool = True


class TextLLMResult(AgentModel):
    text: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    provider: ShortText
    model: ShortText
    prompt_name: ShortText
    prompt_version: ShortText


class NodeMetadata(AgentModel):
    provider: str
    model: str
    prompt_name: str
    prompt_version: str
    prompt_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    schema_version: str | None = Field(default=None, max_length=100)
    request_id: str | None = Field(default=None, max_length=200)
    finish_reason: str | None = Field(default=None, max_length=100)
    latency_ms: int | None = Field(default=None, ge=0)
    attempt_count: int = Field(default=1, ge=1, le=5)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    thinking_mode: str | None = Field(default=None, max_length=20)
    transport_tool_call_count: int = Field(default=0, ge=0)
    json_decoded: bool = True
    schema_validated: bool = True
    invariants_validated: bool = True


class InterpretationNodeResult(AgentModel):
    interpretation: InterpretMessageOutput
    metadata: NodeMetadata


class VerifiedBusinessFact(AgentModel):
    fact_id: UUID
    fact_type: ShortText
    statement: ShortText


class AllowedPolicyEvidence(AgentModel):
    evidence_id: UUID
    statement: ShortText


class SafeErrorInformation(AgentModel):
    code: ShortText
    message: ShortText
    retryable: bool = False


class ComposeResponseInput(AgentModel):
    verified_business_facts: tuple[VerifiedBusinessFact, ...] = Field(max_length=30)
    allowed_policy_evidence: tuple[AllowedPolicyEvidence, ...] = Field(max_length=20)
    current_workflow_stage: WorkflowStage
    task_intent: AgentIntent = AgentIntent.UNKNOWN
    required_user_action: str | None = Field(default=None, max_length=1000)
    safe_error_information: SafeErrorInformation | None = None

    @field_validator("required_user_action")
    @classmethod
    def reject_blank_action(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("required_user_action cannot be blank")
        return value


class ComposeResponseResult(AgentModel):
    response_text: str
    metadata: NodeMetadata

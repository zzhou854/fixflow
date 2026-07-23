"""Strict payload schema for every persisted replay tape step."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.agent.models import InterpretationNodeResult
from app.agent_runtime.models import InterruptPayload
from app.policy.models import PolicyRetrievalResult
from app.property_operations.contracts.appointments import AvailableSlotsData
from app.property_operations.contracts.common import MutationResultData, ToolResponse
from app.property_operations.contracts.properties import ResidentPropertyData
from app.property_operations.contracts.tickets import (
    OpenRepairTicketsData,
    TicketSnapshotData,
)
from app.replay.enums import ReplayStepKind
from app.replay.models import ReplaySafeAgentState, Sha256


class StepPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodeEnteredStep(StepPayload):
    kind: Literal[ReplayStepKind.NODE_ENTERED] = ReplayStepKind.NODE_ENTERED
    node_name: str = Field(min_length=1, max_length=100)
    state_fingerprint: Sha256


class InterpretationResultStep(StepPayload):
    kind: Literal[ReplayStepKind.INTERPRETATION_RESULT] = ReplayStepKind.INTERPRETATION_RESULT
    provider_type: Literal["SCRIPTED", "GLM"]
    schema_version: int = Field(ge=1)
    result: InterpretationNodeResult
    validation_status: Literal["VALIDATED"] = "VALIDATED"
    input_content_hash: Sha256
    provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    prompt_id: str | None = Field(default=None, max_length=100)
    prompt_version: str | None = Field(default=None, max_length=100)
    prompt_hash: Sha256 | None = None
    interpretation_schema_version: str | None = Field(default=None, max_length=100)
    thinking_mode: str | None = Field(default=None, max_length=20)


class PropertyAuthorizationResultStep(StepPayload):
    kind: Literal[ReplayStepKind.PROPERTY_AUTHORIZATION_RESULT] = (
        ReplayStepKind.PROPERTY_AUTHORIZATION_RESULT
    )
    result: ToolResponse[ResidentPropertyData]


class PolicyResultStep(StepPayload):
    kind: Literal[ReplayStepKind.POLICY_RESULT] = ReplayStepKind.POLICY_RESULT
    query_fingerprint: Sha256
    intent_version: int = Field(ge=1)
    result: PolicyRetrievalResult


class DuplicateLookupResultStep(StepPayload):
    kind: Literal[ReplayStepKind.DUPLICATE_LOOKUP_RESULT] = ReplayStepKind.DUPLICATE_LOOKUP_RESULT
    result: ToolResponse[OpenRepairTicketsData]


class TicketSnapshotResultStep(StepPayload):
    kind: Literal[ReplayStepKind.TICKET_SNAPSHOT_RESULT] = ReplayStepKind.TICKET_SNAPSHOT_RESULT
    result: ToolResponse[TicketSnapshotData]


class SlotLookupResultStep(StepPayload):
    kind: Literal[ReplayStepKind.SLOT_LOOKUP_RESULT] = ReplayStepKind.SLOT_LOOKUP_RESULT
    result: ToolResponse[AvailableSlotsData]


class MutationResultStep(StepPayload):
    kind: Literal[ReplayStepKind.MCP_MUTATION_RESULT] = ReplayStepKind.MCP_MUTATION_RESULT
    action: str = Field(min_length=1, max_length=64)
    operation_id: UUID
    request_fingerprint: Sha256
    delivery_classification: str = Field(min_length=1, max_length=40)
    result: ToolResponse[MutationResultData] | None = None
    business_error_code: str | None = Field(default=None, max_length=80)
    reconciliation_case_id: UUID | None = None


class OperatorMutationResultStep(StepPayload):
    kind: Literal[ReplayStepKind.OPERATOR_MUTATION_RESULT] = ReplayStepKind.OPERATOR_MUTATION_RESULT
    action: Literal["ESCALATE_TO_OPERATOR"]
    operation_id: UUID
    request_fingerprint: Sha256
    delivery_classification: str = Field(min_length=1, max_length=40)
    resource_id: UUID | None = None
    resource_version: int | None = Field(default=None, ge=1)
    business_error_code: str | None = Field(default=None, max_length=80)
    reconciliation_case_id: UUID | None = None


class RouteDecisionStep(StepPayload):
    kind: Literal[ReplayStepKind.ROUTE_DECISION] = ReplayStepKind.ROUTE_DECISION
    from_node: str = Field(min_length=1, max_length=100)
    decision: str = Field(min_length=1, max_length=100)
    to_node: str = Field(min_length=1, max_length=100)
    decision_input_fingerprint: Sha256


class InterruptResultStep(StepPayload):
    kind: Literal[ReplayStepKind.INTERRUPT_RESULT] = ReplayStepKind.INTERRUPT_RESULT
    interrupt: InterruptPayload
    candidate_fingerprint: Sha256 | None = None


class FinalStateStep(StepPayload):
    kind: Literal[ReplayStepKind.FINAL_STATE] = ReplayStepKind.FINAL_STATE
    state: ReplaySafeAgentState
    state_fingerprint: Sha256
    route_fingerprint: Sha256


ReplayStepPayload = Annotated[
    NodeEnteredStep
    | InterpretationResultStep
    | PropertyAuthorizationResultStep
    | PolicyResultStep
    | DuplicateLookupResultStep
    | TicketSnapshotResultStep
    | SlotLookupResultStep
    | MutationResultStep
    | OperatorMutationResultStep
    | RouteDecisionStep
    | InterruptResultStep
    | FinalStateStep,
    Field(discriminator="kind"),
]

REPLAY_STEP_ADAPTER: TypeAdapter[ReplayStepPayload] = TypeAdapter(ReplayStepPayload)


class ReplayStepRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence_number: int = Field(ge=1)
    step_kind: ReplayStepKind
    step_key: str = Field(min_length=1, max_length=160)
    request_fingerprint: Sha256 | None = None
    response_schema: str = Field(min_length=1, max_length=160)
    payload: ReplayStepPayload
    step_checksum: Sha256

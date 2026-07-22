"""Shared authoritative operation-outcome MCP contract."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from app.domain.enums import ActorType
from app.property_operations.contracts.common import MCPContractModel


class OperationAction(StrEnum):
    CREATE_TICKET = "CREATE_TICKET"
    BOOK_APPOINTMENT = "BOOK_APPOINTMENT"
    RESCHEDULE_APPOINTMENT = "RESCHEDULE_APPOINTMENT"
    ESCALATE_TO_OPERATOR = "ESCALATE_TO_OPERATOR"


class GetOperationOutcomeRequest(MCPContractModel):
    operation_id: UUID
    action: OperationAction
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_type: ActorType
    actor_id: UUID
    user_id: UUID
    property_id: UUID
    target_entity_type: str | None = None
    target_entity_id: UUID | None = None


class CommittedOperationOutcome(MCPContractModel):
    status: Literal["COMMITTED"] = "COMMITTED"
    operation_id: UUID
    action: OperationAction
    resource_type: str
    resource_id: UUID
    canonical_result: dict[str, object]


class NotCommittedOperationOutcome(MCPContractModel):
    status: Literal["NOT_COMMITTED"] = "NOT_COMMITTED"
    operation_id: UUID
    action: OperationAction


class InconsistentOperationOutcome(MCPContractModel):
    status: Literal["INCONSISTENT"] = "INCONSISTENT"
    operation_id: UUID
    action: OperationAction
    reason_code: str


OperationOutcomeData = Annotated[
    CommittedOperationOutcome | NotCommittedOperationOutcome | InconsistentOperationOutcome,
    Field(discriminator="status"),
]

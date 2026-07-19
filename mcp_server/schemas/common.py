"""Shared MCP request metadata, result envelope, and safe errors."""

from enum import StrEnum
from uuid import UUID

from app.domain.enums import ActorType
from pydantic import BaseModel, ConfigDict, Field


class MCPContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResultCode(StrEnum):
    FOUND = "FOUND"
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    NOT_FOUND = "NOT_FOUND"
    VERSION_CONFLICT = "VERSION_CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    TIME_CONFLICT = "TIME_CONFLICT"
    OPERATION_IN_PROGRESS = "OPERATION_IN_PROGRESS"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    UNKNOWN_COMMIT = "UNKNOWN_COMMIT"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ReadRequest(MCPContractModel):
    actor_type: ActorType
    actor_id: UUID
    trace_id: UUID


class MutationRequest(ReadRequest):
    idempotency_key: str = Field(min_length=1, max_length=128)


class ErrorData(MCPContractModel):
    code: str
    message: str
    field_errors: dict[str, str] = Field(default_factory=dict)
    retryable: bool


class ToolResponse[DataT: BaseModel](MCPContractModel):
    contract_version: str = "1.0"
    result_code: ResultCode
    message: str
    data: DataT | None = None
    error: ErrorData | None = None
    trace_id: UUID


class MutationResultData(MCPContractModel):
    resource_type: str
    resource_id: UUID
    resource_version: int | None = Field(default=None, ge=1)
    replayed: bool = False
    ticket_status: str | None = None
    ticket_version: int | None = Field(default=None, ge=1)
    appointment_status: str | None = None
    appointment_version: int | None = Field(default=None, ge=1)

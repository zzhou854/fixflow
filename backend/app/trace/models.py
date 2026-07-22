"""Closed trace contracts; arbitrary dictionaries are never persistence inputs."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.database.models.observability import (
    AgentRunStatus,
    AgentRunTrigger,
    TraceSource,
)


class TracePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    summary: str | None = None
    error_code: str | None = None
    retryable: bool | None = None
    workflow_stage: str | None = None
    run_status: str | None = None
    interrupt_kind: str | None = None
    node_name: str | None = None
    tool_name: str | None = None
    result_code: str | None = None
    replayed: bool | None = None
    latency_ms: int | None = Field(default=None, ge=0)
    ticket_id: UUID | None = None
    appointment_id: UUID | None = None
    property_id: UUID | None = None
    resident_id: UUID | None = None
    worker_id: UUID | None = None
    aggregate_type: str | None = None
    aggregate_id: UUID | None = None
    aggregate_version: int | None = Field(default=None, ge=1)
    operation_id: UUID | None = None
    from_status: str | None = None
    to_status: str | None = None
    status: str | None = None
    action: str | None = None
    issue_category: str | None = None
    severity: str | None = None
    purpose: str | None = None
    reason_code: str | None = None
    starts_at: str | None = None
    ends_at: str | None = None
    previous_appointment_id: UUID | None = None
    original_run_id: UUID | None = None
    idempotency_key_fingerprint: str | None = None


class StartRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    run_id: UUID
    thread_id: UUID | None
    trace_id: UUID
    trigger: AgentRunTrigger
    actor_type: str
    actor_id: UUID
    user_id: UUID | None = None
    property_id: UUID | None = None
    initial_intent_version: int | None = Field(default=None, ge=0)
    started_at: datetime


class AgentRunRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    run_id: UUID = Field(validation_alias="id")
    thread_id: UUID | None
    trace_id: UUID
    trigger: AgentRunTrigger
    status: AgentRunStatus
    actor_type: str
    actor_id: UUID
    user_id: UUID | None
    property_id: UUID | None
    initial_intent_version: int | None
    final_intent_version: int | None
    started_at: datetime
    finished_at: datetime | None
    terminal_event_type: str | None
    error_code: str | None


class TraceEventRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    event_id: UUID = Field(validation_alias="id")
    event_key: str
    run_id: UUID | None
    thread_id: UUID | None
    trace_id: UUID
    sequence_number: int | None
    source: TraceSource
    event_type: str
    node_name: str | None
    operation_id: UUID | None
    payload: dict[str, object]
    occurred_at: datetime

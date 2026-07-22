from datetime import datetime
from uuid import UUID

from app.api.schemas.common import ApiModel
from app.infrastructure.database.models.observability import (
    AgentRunStatus,
    AgentRunTrigger,
    TraceSource,
)


class AgentRunResponse(ApiModel):
    run_id: UUID
    thread_id: UUID | None
    trace_id: UUID
    trigger: AgentRunTrigger
    status: AgentRunStatus
    started_at: datetime
    finished_at: datetime | None
    error_code: str | None


class AgentRunPageResponse(ApiModel):
    items: tuple[AgentRunResponse, ...]
    limit: int
    offset: int


class TraceEventResponse(ApiModel):
    event_id: UUID
    sequence_number: int | None
    source: TraceSource
    event_type: str
    node_name: str | None
    operation_id: UUID | None
    payload: dict[str, object]
    occurred_at: datetime


class TraceEventPageResponse(ApiModel):
    items: tuple[TraceEventResponse, ...]
    limit: int
    offset: int

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ClaimedOutboxEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    event_id: UUID = Field(validation_alias="id")
    claim_token: UUID
    event_key: str
    event_type: str
    aggregate_type: str
    aggregate_id: UUID
    aggregate_version: int
    operation_id: UUID
    trace_id: UUID
    run_id: UUID | None
    thread_id: UUID | None
    actor_type: str
    actor_id: UUID
    payload: dict[str, object]
    attempt_count: int
    occurred_at: datetime
    available_at: datetime
    created_at: datetime

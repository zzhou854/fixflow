"""Sanitized Operator Recovery Console contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.infrastructure.database.models.observability import AgentRunStatus, AgentRunTrigger
from app.replay.enums import (
    RecoveryRecommendation,
    ReplayBundleStatus,
    ReplayExecutionStatus,
    ReplayMismatchType,
)


class ReplayApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplayMismatchResponse(ReplayApiModel):
    mismatch_type: ReplayMismatchType
    step_key: str | None = None
    expected_summary: str | None = None
    actual_summary: str | None = None


class ReplayExecutionResponse(ReplayApiModel):
    execution_id: UUID
    bundle_id: UUID
    status: ReplayExecutionStatus
    runtime_revision: str
    graph_schema_version: int
    started_at: datetime
    completed_at: datetime | None
    actual_route_fingerprint: str | None
    actual_state_fingerprint: str | None
    mismatches: tuple[ReplayMismatchResponse, ...]
    recommendation: RecoveryRecommendation | None
    error_code: str | None


class ReplayBundleResponse(ReplayApiModel):
    bundle_id: UUID
    original_run_id: UUID
    status: ReplayBundleStatus
    schema_version: int
    graph_schema_version: int
    runtime_revision: str
    artifact_integrity: str
    expected_route_fingerprint: str | None
    expected_state_fingerprint: str | None
    step_count: int
    capture_error_code: str | None
    captured_at: datetime
    finalized_at: datetime | None
    latest_execution: ReplayExecutionResponse | None = None


class ReplayRunItemResponse(ReplayApiModel):
    run_id: UUID
    thread_id: UUID | None
    trigger_type: AgentRunTrigger
    original_run_status: AgentRunStatus
    replayability: ReplayBundleStatus
    bundle_id: UUID | None
    bundle_status: ReplayBundleStatus
    latest_replay_status: ReplayExecutionStatus | None
    started_at: datetime


class ReplayRunPageResponse(ReplayApiModel):
    items: tuple[ReplayRunItemResponse, ...]
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ReplayRunDetailResponse(ReplayApiModel):
    run: ReplayRunItemResponse
    bundle: ReplayBundleResponse | None
    current_business_state: dict[str, str | int | bool | None] | None = None

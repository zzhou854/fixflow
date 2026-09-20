"""Persistent deterministic-replay control-plane artifacts."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import string_enum
from app.infrastructure.database.models.observability import AgentRunTrigger
from app.replay.enums import ReplayBundleStatus, ReplayExecutionStatus, ReplayStepKind


class AgentReplayBundle(Base):
    __tablename__ = "agent_replay_bundles"
    __table_args__ = (
        UniqueConstraint("bundle_id", name="uq_agent_replay_bundles_bundle_id"),
        UniqueConstraint("original_run_id", name="uq_agent_replay_bundles_original_run"),
        CheckConstraint("schema_version > 0", name="schema_version_positive"),
        CheckConstraint("graph_schema_version > 0", name="graph_schema_version_positive"),
        CheckConstraint("step_count >= 0", name="step_count_nonnegative"),
        CheckConstraint(
            "(status = 'CAPTURING' AND finalized_at IS NULL AND bundle_checksum IS NULL) OR "
            "(status <> 'CAPTURING' AND finalized_at IS NOT NULL)",
            name="finalization_matches_status",
        ),
        CheckConstraint(
            "status <> 'READY' OR "
            "(start_state_payload IS NOT NULL AND expected_result_payload IS NOT NULL "
            "AND expected_route_fingerprint IS NOT NULL "
            "AND expected_state_fingerprint IS NOT NULL AND bundle_checksum IS NOT NULL)",
            name="ready_bundle_is_complete",
        ),
        Index("ix_agent_replay_bundles_thread_captured", "thread_id", "captured_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    bundle_id: Mapped[UUID] = mapped_column(nullable=False)
    original_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False
    )
    thread_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    original_trace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    trigger_type: Mapped[AgentRunTrigger] = mapped_column(
        string_enum(AgentRunTrigger, name="replay_trigger_type_values"), nullable=False
    )
    status: Mapped[ReplayBundleStatus] = mapped_column(
        string_enum(ReplayBundleStatus, name="replay_bundle_status_values"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(nullable=False)
    graph_schema_version: Mapped[int] = mapped_column(nullable=False)
    runtime_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    start_state_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    input_envelope: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    expected_result_payload: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    expected_route_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_state_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step_count: Mapped[int] = mapped_column(nullable=False, default=0)
    bundle_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    capture_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )


class AgentReplayStep(Base):
    __tablename__ = "agent_replay_steps"
    __table_args__ = (
        UniqueConstraint(
            "bundle_id", "sequence_number", name="uq_agent_replay_steps_bundle_sequence"
        ),
        UniqueConstraint("bundle_id", "step_key", name="uq_agent_replay_steps_bundle_key"),
        CheckConstraint("sequence_number > 0", name="sequence_number_positive"),
        Index("ix_agent_replay_steps_bundle_sequence", "bundle_id", "sequence_number"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    bundle_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_replay_bundles.bundle_id", ondelete="RESTRICT"), nullable=False
    )
    sequence_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    step_kind: Mapped[ReplayStepKind] = mapped_column(
        string_enum(ReplayStepKind, name="replay_step_kind_values"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_schema: Mapped[str] = mapped_column(String(160), nullable=False)
    response_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    step_checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AgentReplayExecution(Base):
    __tablename__ = "agent_replay_executions"
    __table_args__ = (
        UniqueConstraint("execution_id", name="uq_agent_replay_executions_execution_id"),
        UniqueConstraint(
            "requested_by_actor_id",
            "request_key_fingerprint",
            name="uq_agent_replay_executions_actor_request_key",
        ),
        CheckConstraint("graph_schema_version > 0", name="graph_schema_version_positive"),
        CheckConstraint("mismatch_count >= 0", name="mismatch_count_nonnegative"),
        CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="completion_matches_status",
        ),
        CheckConstraint(
            "(status = 'RUNNING' AND comparison_summary IS NULL AND recommendation IS NULL) OR "
            "(status <> 'RUNNING' AND comparison_summary IS NOT NULL "
            "AND recommendation IS NOT NULL)",
            name="result_matches_status",
        ),
        Index("ix_agent_replay_executions_bundle_started", "bundle_id", "started_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    execution_id: Mapped[UUID] = mapped_column(nullable=False)
    bundle_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_replay_bundles.bundle_id", ondelete="RESTRICT"), nullable=False
    )
    requested_by_actor_id: Mapped[UUID] = mapped_column(nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(nullable=False)
    request_key_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ReplayExecutionStatus] = mapped_column(
        string_enum(ReplayExecutionStatus, name="replay_execution_status_values"),
        nullable=False,
    )
    runtime_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    graph_schema_version: Mapped[int] = mapped_column(nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actual_route_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    actual_state_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mismatch_count: Mapped[int] = mapped_column(nullable=False, default=0)
    comparison_summary: Mapped[dict[str, object] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=text("CURRENT_TIMESTAMP"),
    )

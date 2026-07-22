"""Persistent Agent runs, sanitized trace events, and transactional outbox rows."""

from datetime import datetime
from enum import StrEnum
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


class AgentRunStatus(StrEnum):
    RUNNING = "RUNNING"
    INTERRUPTED = "INTERRUPTED"
    COMPLETED = "COMPLETED"
    FAILED_SAFE = "FAILED_SAFE"
    FAILED = "FAILED"


class AgentRunTrigger(StrEnum):
    THREAD_CREATED = "THREAD_CREATED"
    MESSAGE = "MESSAGE"
    RESUME = "RESUME"
    OPERATOR_ACTION = "OPERATOR_ACTION"


class TraceSource(StrEnum):
    API = "API"
    AGENT = "AGENT"
    MCP = "MCP"
    DOMAIN = "DOMAIN"
    OUTBOX = "OUTBOX"
    RECONCILIATION = "RECONCILIATION"


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    DISPATCHED = "DISPATCHED"
    DEAD_LETTER = "DEAD_LETTER"


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint("trace_id", name="uq_agent_runs_trace_id"),
        CheckConstraint(
            "actor_type IN ('RESIDENT','OPERATOR','WORKER','SYSTEM')",
            name="actor_type_values",
        ),
        CheckConstraint("last_sequence >= 0", name="last_sequence_nonnegative"),
        CheckConstraint(
            "(status = 'RUNNING' AND finished_at IS NULL) OR "
            "(status <> 'RUNNING' AND finished_at IS NOT NULL)",
            name="finish_time_matches_status",
        ),
        CheckConstraint(
            "(status = 'RUNNING' AND terminal_event_type IS NULL) OR "
            "(status = 'INTERRUPTED' AND terminal_event_type = 'run_interrupted') OR "
            "(status = 'COMPLETED' AND terminal_event_type = 'run_completed') OR "
            "(status = 'FAILED_SAFE' AND terminal_event_type = 'run_failed_safe') OR "
            "(status = 'FAILED' AND terminal_event_type = 'run_failed')",
            name="terminal_event_matches_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    thread_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    trace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    trigger: Mapped[AgentRunTrigger] = mapped_column(
        string_enum(AgentRunTrigger, name="agent_run_trigger_values"), nullable=False
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        string_enum(AgentRunStatus, name="agent_run_status_values"), nullable=False
    )
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    property_id: Mapped[UUID | None] = mapped_column(nullable=True)
    initial_intent_version: Mapped[int | None] = mapped_column(nullable=True)
    final_intent_version: Mapped[int | None] = mapped_column(nullable=True)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    terminal_event_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class AgentTraceEvent(Base):
    __tablename__ = "agent_trace_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_agent_trace_events_event_key"),
        UniqueConstraint("run_id", "sequence_number", name="uq_agent_trace_events_run_sequence"),
        Index(
            "uq_agent_trace_events_lifecycle_terminal_per_run",
            "run_id",
            unique=True,
            postgresql_where=text(
                "event_type IN ('run_interrupted','run_completed','run_failed_safe','run_failed')"
            ),
        ),
        CheckConstraint(
            "(run_id IS NULL AND sequence_number IS NULL) OR "
            "(run_id IS NOT NULL AND sequence_number > 0)",
            name="run_sequence_pair_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    thread_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    trace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    sequence_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source: Mapped[TraceSource] = mapped_column(
        string_enum(TraceSource, name="agent_trace_source_values"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    node_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    operation_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_outbox_events_event_key"),
        CheckConstraint("aggregate_version > 0", name="aggregate_version_positive"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "actor_type IN ('RESIDENT','OPERATOR','WORKER','SYSTEM')",
            name="actor_type_values",
        ),
        CheckConstraint(
            "(status = 'PROCESSING' AND claimed_by IS NOT NULL AND claim_token IS NOT NULL "
            "AND claim_expires_at IS NOT NULL) OR "
            "(status <> 'PROCESSING' AND claimed_by IS NULL AND claim_token IS NULL "
            "AND claim_expires_at IS NULL)",
            name="claim_matches_status",
        ),
        CheckConstraint(
            "(status = 'DISPATCHED' AND dispatched_at IS NOT NULL) OR "
            "(status <> 'DISPATCHED' AND dispatched_at IS NULL)",
            name="dispatch_time_matches_status",
        ),
        Index("ix_outbox_events_dispatch", "status", "available_at", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    event_key: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    aggregate_version: Mapped[int] = mapped_column(nullable=False)
    operation_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    trace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    run_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    thread_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(
        string_enum(OutboxStatus, name="outbox_status_values"),
        nullable=False,
        default=OutboxStatus.PENDING,
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_token: Mapped[UUID | None] = mapped_column(nullable=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

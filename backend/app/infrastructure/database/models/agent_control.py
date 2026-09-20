"""Durable Agent messages, thread registry, and human-review work items."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.application.agent_reliability_models import (
    AgentMessageRole,
    HumanReviewFailureStage,
    HumanReviewSafetyLevel,
    HumanReviewStatus,
    ThreadLifecycleStatus,
    ThreadPropertyResolutionStatus,
)
from app.domain.enums import ActorType
from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import string_enum


class AgentThreadRecordRow(Base):
    __tablename__ = "agent_thread_records"
    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "(lifecycle_status = 'ACTIVE' AND archived_at IS NULL AND deleted_at IS NULL) OR "
            "(lifecycle_status = 'ARCHIVED' AND archived_at IS NOT NULL AND deleted_at IS NULL) OR "
            "(lifecycle_status = 'DELETED' AND archived_at IS NOT NULL AND deleted_at IS NOT NULL)",
            name="archive_time_matches_status",
        ),
        CheckConstraint(
            "(property_resolution_status = 'RESOLVED' AND property_id IS NOT NULL) OR "
            "(property_resolution_status <> 'RESOLVED')",
            name="resolved_property_present",
        ),
        Index(
            "ix_agent_thread_records_resident_lifecycle_activity",
            "resident_id",
            "lifecycle_status",
            text("last_activity_at DESC"),
        ),
    )

    thread_id: Mapped[UUID] = mapped_column(primary_key=True)
    resident_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    property_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("properties.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    property_resolution_status: Mapped[ThreadPropertyResolutionStatus] = mapped_column(
        string_enum(
            ThreadPropertyResolutionStatus,
            name="agent_thread_property_resolution_status_values",
        ),
        nullable=False,
    )
    lifecycle_status: Mapped[ThreadLifecycleStatus] = mapped_column(
        string_enum(ThreadLifecycleStatus, name="agent_thread_lifecycle_status_values"),
        nullable=False,
    )
    display_title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_by_actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class AgentMessageRow(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_agent_messages_message_id"),
        UniqueConstraint("thread_id", "sequence_no", name="uq_agent_messages_thread_sequence"),
        UniqueConstraint("run_id", "role", name="uq_agent_messages_run_role"),
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
        CheckConstraint("length(btrim(content)) > 0", name="content_not_blank"),
        Index("ix_agent_messages_thread_created", "thread_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(nullable=False)
    thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_thread_records.thread_id", ondelete="RESTRICT"), nullable=False
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    role: Mapped[AgentMessageRole] = mapped_column(
        string_enum(AgentMessageRole, name="agent_message_role_values"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HumanReviewCaseRow(Base):
    __tablename__ = "human_review_cases"
    __table_args__ = (
        CheckConstraint("intent_version > 0", name="intent_version_positive"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("priority BETWEEN 1 AND 100", name="priority_range"),
        CheckConstraint("length(btrim(summary)) > 0", name="summary_not_blank"),
        CheckConstraint(
            "(status = 'CLAIMED' AND assigned_operator_id IS NOT NULL "
            "AND claimed_at IS NOT NULL) OR "
            "(status <> 'CLAIMED')",
            name="claim_fields_match_status",
        ),
        CheckConstraint(
            "(status IN ('RESOLVED','DISMISSED') AND resolved_at IS NOT NULL) OR "
            "(status NOT IN ('RESOLVED','DISMISSED') AND resolved_at IS NULL)",
            name="resolution_time_matches_status",
        ),
        Index(
            "uq_human_review_cases_active_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status IN ('OPEN','CLAIMED')"),
        ),
        Index("ix_human_review_cases_queue", "status", "priority", "created_at"),
        Index("ix_human_review_cases_thread", "thread_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    thread_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_thread_records.thread_id", ondelete="RESTRICT"), nullable=False
    )
    resident_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    property_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("properties.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    ticket_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("repair_tickets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    source_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    intent_version: Mapped[int] = mapped_column(Integer, nullable=False)
    failure_stage: Mapped[HumanReviewFailureStage] = mapped_column(
        string_enum(HumanReviewFailureStage, name="human_review_failure_stage_values"),
        nullable=False,
    )
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    safety_level: Mapped[HumanReviewSafetyLevel] = mapped_column(
        string_enum(HumanReviewSafetyLevel, name="human_review_safety_level_values"),
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[HumanReviewStatus] = mapped_column(
        string_enum(HumanReviewStatus, name="human_review_status_values"), nullable=False
    )
    assigned_operator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class HumanReviewCaseEventRow(Base):
    __tablename__ = "human_review_case_events"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "sequence_no",
            name="uq_human_review_case_events_case_sequence",
        ),
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
        CheckConstraint("version_before >= 0", name="version_before_nonnegative"),
        CheckConstraint("version_after > version_before", name="version_increases"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    case_id: Mapped[UUID] = mapped_column(
        ForeignKey("human_review_cases.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    from_status: Mapped[HumanReviewStatus | None] = mapped_column(
        string_enum(HumanReviewStatus, name="human_review_event_from_status_values"),
        nullable=True,
    )
    to_status: Mapped[HumanReviewStatus] = mapped_column(
        string_enum(HumanReviewStatus, name="human_review_event_to_status_values"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        string_enum(ActorType, name="human_review_event_actor_type_values"),
        nullable=False,
    )
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    trace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

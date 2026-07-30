"""Sanitized, non-authoritative online-provider shadow observations."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import string_enum


class LLMShadowResultStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class LLMShadowRun(Base):
    __tablename__ = "llm_shadow_runs"
    __table_args__ = (
        CheckConstraint("latency_ms >= 0", name="latency_nonnegative"),
        CheckConstraint(
            "(result_status = 'SUCCEEDED' AND structured_result_hash IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(result_status = 'FAILED' AND structured_result_hash IS NULL "
            "AND error_code IS NOT NULL)",
            name="result_payload_matches_status",
        ),
        Index("ix_llm_shadow_runs_source_created", "source_run_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    result_status: Mapped[LLMShadowResultStatus] = mapped_column(
        string_enum(LLMShadowResultStatus, name="llm_shadow_result_status_values"),
        nullable=False,
    )
    latency_ms: Mapped[int] = mapped_column(nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    structured_result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

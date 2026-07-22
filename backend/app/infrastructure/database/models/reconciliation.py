"""Persistent UNKNOWN_COMMIT reconciliation control-plane model."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import ActorType
from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class ReconciliationAction(StrEnum):
    CREATE_TICKET = "CREATE_TICKET"
    BOOK_APPOINTMENT = "BOOK_APPOINTMENT"
    RESCHEDULE_APPOINTMENT = "RESCHEDULE_APPOINTMENT"
    ESCALATE_TO_OPERATOR = "ESCALATE_TO_OPERATOR"


class ReconciliationStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    RESOLVED_COMMITTED = "RESOLVED_COMMITTED"
    RESOLVED_NOT_COMMITTED = "RESOLVED_NOT_COMMITTED"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class EvidenceStatus(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    INCONSISTENT = "INCONSISTENT"


class OperationReconciliationCase(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "operation_reconciliation_cases"
    __table_args__ = (
        UniqueConstraint("case_key", name="uq_operation_reconciliation_cases_case_key"),
        UniqueConstraint("operation_id", name="uq_operation_reconciliation_cases_operation_id"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "(status = 'PROCESSING' AND claimed_by IS NOT NULL AND claim_token IS NOT NULL "
            "AND claim_expires_at IS NOT NULL) OR (status <> 'PROCESSING' AND claimed_by IS NULL "
            "AND claim_token IS NULL AND claim_expires_at IS NULL)",
            name="claim_matches_status",
        ),
        CheckConstraint(
            "(status IN ('RESOLVED_COMMITTED','RESOLVED_NOT_COMMITTED','MANUAL_REVIEW') "
            "AND resolved_at IS NOT NULL) OR "
            "(status IN ('PENDING','PROCESSING') AND resolved_at IS NULL)",
            name="resolution_matches_status",
        ),
        Index("ix_reconciliation_claim", "status", "available_at", "created_at"),
    )

    case_key: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    action: Mapped[ReconciliationAction] = mapped_column(
        string_enum(ReconciliationAction, name="reconciliation_action_values"), nullable=False
    )
    thread_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    original_run_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    original_trace_id: Mapped[UUID] = mapped_column(nullable=False)
    operation_idempotency_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        string_enum(ActorType, name="reconciliation_actor_type_values"), nullable=False
    )
    actor_id: Mapped[UUID] = mapped_column(nullable=False)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    property_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    target_entity_type: Mapped[str | None] = mapped_column(String(40))
    target_entity_id: Mapped[UUID | None] = mapped_column(index=True)
    expected_entity_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[ReconciliationStatus] = mapped_column(
        string_enum(ReconciliationStatus, name="reconciliation_status_values"),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    last_evidence_status: Mapped[EvidenceStatus | None] = mapped_column(
        string_enum(EvidenceStatus, name="reconciliation_evidence_status_values")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    claimed_by: Mapped[str | None] = mapped_column(String(128))
    claim_token: Mapped[UUID | None]
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_code: Mapped[str | None] = mapped_column(String(80))
    safe_result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_message: Mapped[str | None] = mapped_column(Text)

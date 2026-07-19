"""Persistence-only idempotency record model."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import ActorType
from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class IdempotencyExecutionStatus(StrEnum):
    """Lifecycle of a stored mutation attempt."""

    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class IdempotencyRecord(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Unique mutation identity and its durable result envelope."""

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "actor_type",
            "actor_id",
            "idempotency_key",
            name="uq_idempotency_records_operation_actor_key",
        ),
    )

    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        string_enum(ActorType, name="idempotency_actor_type_values"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_status: Mapped[IdempotencyExecutionStatus] = mapped_column(
        string_enum(IdempotencyExecutionStatus, name="idempotency_execution_status_values"),
        nullable=False,
        server_default=text("'PENDING'"),
    )
    resource_type: Mapped[str | None] = mapped_column(String(80))
    resource_id: Mapped[UUID | None] = mapped_column()
    response_payload: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    def __repr__(self) -> str:
        return (
            f"IdempotencyRecord(id={self.id!r}, scope={self.scope!r}, "
            f"execution_status={self.execution_status!r})"
        )

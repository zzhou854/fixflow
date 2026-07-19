"""Canonical append-only worker-event persistence model."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import ActorType, WorkerEventType
from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import UUIDPrimaryKeyMixin, string_enum


class WorkerEvent(UUIDPrimaryKeyMixin, Base):
    """Accepted worker evidence; sequence legality stays in the domain layer."""

    __tablename__ = "worker_events"
    __table_args__ = (
        UniqueConstraint(
            "appointment_id",
            "sequence_no",
            name="uq_worker_events_appointment_sequence",
        ),
        UniqueConstraint("external_event_key", name="uq_worker_events_external_event_key"),
        CheckConstraint("sequence_no > 0", name="sequence_positive"),
    )

    appointment_id: Mapped[UUID] = mapped_column(
        ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    subject_worker_id: Mapped[UUID] = mapped_column(
        ForeignKey("workers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    sequence_no: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[WorkerEventType] = mapped_column(
        string_enum(WorkerEventType, name="worker_event_type_values"), nullable=False
    )
    actor_type: Mapped[ActorType] = mapped_column(
        string_enum(ActorType, name="worker_event_actor_type_values"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    external_event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    def __repr__(self) -> str:
        return (
            f"WorkerEvent(id={self.id!r}, appointment_id={self.appointment_id!r}, "
            f"sequence_no={self.sequence_no!r}, event_type={self.event_type!r})"
        )

"""Formal appointment snapshot and append-only status history mappings."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, TSTZRANGE, ExcludeConstraint
from sqlalchemy.dialects.postgresql.ranges import Range
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import ActorType, AppointmentPurpose, AppointmentStatus
from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)

if TYPE_CHECKING:
    from app.infrastructure.database.models.ticket import RepairTicket
    from app.infrastructure.database.models.worker import Worker


class Appointment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Formal immutable-interval appointment snapshot."""

    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("NOT isempty(scheduled_range)", name="scheduled_range_nonempty"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "supersedes_appointment_id IS NULL OR supersedes_appointment_id <> id",
            name="not_self_superseding",
        ),
        CheckConstraint(
            "(status IN ('CANCELLED', 'NO_SHOW') AND outcome_actor_type IS NOT NULL "
            "AND outcome_actor_id IS NOT NULL AND outcome_reason_code IS NOT NULL "
            "AND outcome_reason_text IS NOT NULL AND outcome_evidence IS NOT NULL "
            "AND outcome_occurred_at IS NOT NULL) OR "
            "(status NOT IN ('CANCELLED', 'NO_SHOW') AND outcome_actor_type IS NULL "
            "AND outcome_actor_id IS NULL AND outcome_reason_code IS NULL "
            "AND outcome_reason_text IS NULL AND outcome_evidence IS NULL "
            "AND outcome_occurred_at IS NULL)",
            name="terminal_outcome_metadata_consistent",
        ),
        Index(
            "uq_appointments_ticket_booked",
            "ticket_id",
            unique=True,
            postgresql_where=text("status = 'BOOKED'"),
        ),
        ExcludeConstraint(
            ("worker_id", "="),
            ("scheduled_range", "&&"),
            where=text("status = 'BOOKED'"),
            using="gist",
            name="ex_appointments_worker_booked_overlap",
        ),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("repair_tickets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    worker_id: Mapped[UUID] = mapped_column(
        ForeignKey("workers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    purpose: Mapped[AppointmentPurpose] = mapped_column(
        string_enum(AppointmentPurpose, name="appointment_purpose_values"), nullable=False
    )
    status: Mapped[AppointmentStatus] = mapped_column(
        string_enum(AppointmentStatus, name="appointment_status_values"),
        nullable=False,
        server_default=text("'BOOKED'"),
    )
    scheduled_range: Mapped[Range[datetime]] = mapped_column(TSTZRANGE, nullable=False)
    supersedes_appointment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    outcome_actor_type: Mapped[ActorType | None] = mapped_column(
        string_enum(ActorType, name="appointment_outcome_actor_type_values")
    )
    outcome_actor_id: Mapped[str | None] = mapped_column(String(64))
    outcome_reason_code: Mapped[str | None] = mapped_column(String(80))
    outcome_reason_text: Mapped[str | None] = mapped_column(String(1000))
    outcome_evidence: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    outcome_occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    ticket: Mapped[RepairTicket] = relationship()
    worker: Mapped[Worker] = relationship()
    supersedes: Mapped[Appointment | None] = relationship(
        remote_side="Appointment.id", foreign_keys=[supersedes_appointment_id]
    )
    status_history: Mapped[list[AppointmentStatusHistory]] = relationship(
        back_populates="appointment"
    )


class AppointmentStatusHistory(UUIDPrimaryKeyMixin, Base):
    """Append-only evidence for a formal appointment transition."""

    __tablename__ = "appointment_status_history"
    __table_args__ = (
        CheckConstraint("version_before >= 0", name="version_before_nonnegative"),
        CheckConstraint("version_after = version_before + 1", name="version_step"),
    )

    appointment_id: Mapped[UUID] = mapped_column(
        ForeignKey("appointments.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    from_status: Mapped[AppointmentStatus | None] = mapped_column(
        string_enum(AppointmentStatus, name="appointment_history_from_status_values")
    )
    to_status: Mapped[AppointmentStatus] = mapped_column(
        string_enum(AppointmentStatus, name="appointment_history_to_status_values"),
        nullable=False,
    )
    actor_type: Mapped[ActorType] = mapped_column(
        string_enum(ActorType, name="appointment_history_actor_type_values"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    reason_text: Mapped[str | None] = mapped_column(String(1000))
    evidence: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    trace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    appointment: Mapped[Appointment] = relationship(back_populates="status_history")

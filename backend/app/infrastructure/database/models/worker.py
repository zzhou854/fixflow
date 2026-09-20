"""Maintenance-worker capabilities and availability mappings."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import TSTZRANGE
from sqlalchemy.dialects.postgresql.ranges import Range
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import WorkerSkillType
from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class Worker(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Maintenance worker available for deterministic matching."""

    __tablename__ = "workers"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    service_area: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    skills: Mapped[list[WorkerSkill]] = relationship(back_populates="worker")
    availability: Mapped[list[WorkerAvailability]] = relationship(back_populates="worker")


class WorkerSkill(Base):
    """One typed capability held by a maintenance worker."""

    __tablename__ = "worker_skills"
    worker_id: Mapped[UUID] = mapped_column(
        ForeignKey("workers.id", ondelete="RESTRICT"), primary_key=True
    )
    skill_type: Mapped[WorkerSkillType] = mapped_column(
        string_enum(WorkerSkillType, name="worker_skill_type_values"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    worker: Mapped[Worker] = relationship(back_populates="skills")


class WorkerAvailability(UUIDPrimaryKeyMixin, Base):
    """Declared worker availability; formal bookings carry the overlap guard."""

    __tablename__ = "worker_availability"
    __table_args__ = (
        CheckConstraint("NOT isempty(available_range)", name="range_nonempty"),
        UniqueConstraint(
            "worker_id",
            "available_range",
            name="uq_worker_availability_worker_available_range",
        ),
    )

    worker_id: Mapped[UUID] = mapped_column(
        ForeignKey("workers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    available_range: Mapped[Range[datetime]] = mapped_column(TSTZRANGE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    worker: Mapped[Worker] = relationship(back_populates="availability")

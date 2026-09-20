"""User, property, and resident authorization persistence models."""

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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import TimestampMixin, UUIDPrimaryKeyMixin


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Resident or property-operator identity."""

    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('RESIDENT', 'OPERATOR')", name="role_values"),)

    username: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    resident_properties: Mapped[list[ResidentPropertyRelation]] = relationship(
        back_populates="resident"
    )

    def __repr__(self) -> str:
        return f"User(id={self.id!r}, username={self.username!r}, role={self.role!r})"


class Property(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A serviceable residential unit."""

    __tablename__ = "properties"
    __table_args__ = (
        UniqueConstraint(
            "community_name",
            "building_no",
            "unit_no",
            "room_no",
            name="uq_properties_property_identity",
        ),
    )

    community_name: Mapped[str] = mapped_column(String(100), nullable=False)
    building_no: Mapped[str] = mapped_column(String(30), nullable=False)
    unit_no: Mapped[str] = mapped_column(String(30), nullable=False)
    room_no: Mapped[str] = mapped_column(String(30), nullable=False)
    address_text: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    resident_relations: Mapped[list[ResidentPropertyRelation]] = relationship(
        back_populates="property"
    )


class ResidentPropertyRelation(UUIDPrimaryKeyMixin, Base):
    """Persisted authorization link between a resident and a property."""

    __tablename__ = "resident_property_relations"
    __table_args__ = (
        UniqueConstraint(
            "resident_id",
            "property_id",
            name="uq_resident_property_relations_resident_property",
        ),
    )

    resident_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    property_id: Mapped[UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("CURRENT_TIMESTAMP"), nullable=False
    )

    resident: Mapped[User] = relationship(back_populates="resident_properties")
    property: Mapped[Property] = relationship(back_populates="resident_relations")

"""Small reusable SQLAlchemy column helpers for database models."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, func
from sqlalchemy.orm import Mapped, mapped_column


def string_enum(enum_type: type[StrEnum], *, name: str) -> Enum:
    """Store a Python string enum as VARCHAR with a named CHECK constraint."""

    return Enum(
        enum_type,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
        name=name,
    )


class UUIDPrimaryKeyMixin:
    """Python-generated UUID primary key."""

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)


class TimestampMixin:
    """Database-created timestamp plus application-managed update timestamp."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

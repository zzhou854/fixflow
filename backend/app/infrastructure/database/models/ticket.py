"""Repair-ticket aggregate and append-only status history mappings."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import ActorType, IssueCategory, Severity, TicketStatus
from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class RepairTicket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current repair-ticket snapshot; transition logic remains in the domain."""

    __tablename__ = "repair_tickets"
    __table_args__ = (
        CheckConstraint("rework_count >= 0", name="rework_count_nonnegative"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint(
            "(status = 'ESCALATED' AND escalated_from_status IS NOT NULL) OR "
            "(status <> 'ESCALATED' AND escalated_from_status IS NULL)",
            name="escalation_prior_consistent",
        ),
        CheckConstraint(
            "escalated_from_status IS NULL OR escalated_from_status IN "
            "('OPEN', 'SCHEDULED', 'IN_PROGRESS', 'PENDING_ACCEPTANCE', "
            "'REWORK_REQUIRED')",
            name="escalation_prior_values",
        ),
        CheckConstraint(
            "length(btrim(issue_location)) > 0 AND length(btrim(issue_description)) > 0",
            name="issue_description_present",
        ),
    )

    resident_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    property_id: Mapped[UUID] = mapped_column(
        ForeignKey("properties.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    issue_category: Mapped[IssueCategory] = mapped_column(
        string_enum(IssueCategory, name="issue_category_values"), nullable=False
    )
    issue_location: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[Severity] = mapped_column(
        string_enum(Severity, name="severity_values"), nullable=False
    )
    status: Mapped[TicketStatus] = mapped_column(
        string_enum(TicketStatus, name="ticket_status_values"),
        nullable=False,
        server_default=text("'OPEN'"),
    )
    escalated_from_status: Mapped[TicketStatus | None] = mapped_column(
        string_enum(TicketStatus, name="ticket_escalated_from_status_values"), nullable=True
    )
    rework_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status_history: Mapped[list[TicketStatusHistory]] = relationship(back_populates="ticket")


class TicketStatusHistory(UUIDPrimaryKeyMixin, Base):
    """Append-only evidence for an accepted ticket transition."""

    __tablename__ = "ticket_status_history"
    __table_args__ = (
        CheckConstraint("version_before >= 0", name="version_before_nonnegative"),
        CheckConstraint("version_after = version_before + 1", name="version_step"),
    )

    ticket_id: Mapped[UUID] = mapped_column(
        ForeignKey("repair_tickets.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    from_status: Mapped[TicketStatus | None] = mapped_column(
        string_enum(TicketStatus, name="ticket_history_from_status_values")
    )
    to_status: Mapped[TicketStatus] = mapped_column(
        string_enum(TicketStatus, name="ticket_history_to_status_values"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(
        string_enum(ActorType, name="ticket_history_actor_type_values"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    reason_text: Mapped[str | None] = mapped_column(String(1000))
    evidence: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    trace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    version_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    version_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    ticket: Mapped[RepairTicket] = relationship(back_populates="status_history")

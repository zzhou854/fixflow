"""Create core property repair schema.

Revision ID: 20260719_0001
Revises:
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260719_0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TICKET_STATUSES = (
    "OPEN",
    "SCHEDULED",
    "IN_PROGRESS",
    "PENDING_ACCEPTANCE",
    "REWORK_REQUIRED",
    "ESCALATED",
    "CANCELLED",
    "CLOSED",
)
APPOINTMENT_STATUSES = ("BOOKED", "FULFILLED", "SUPERSEDED", "CANCELLED", "NO_SHOW")
APPOINTMENT_PURPOSES = ("INITIAL_REPAIR", "REWORK")
WORKER_EVENT_TYPES = (
    "ACCEPTED",
    "REJECTED",
    "DEPARTED",
    "ARRIVED",
    "STARTED",
    "COMPLETED",
    "FAILED_TO_COMPLETE",
    "CANCELLED",
    "NO_SHOW",
)
ACTOR_TYPES = ("RESIDENT", "OPERATOR", "WORKER", "SYSTEM")
ISSUE_CATEGORIES = ("WATER_LEAK", "ELECTRICAL", "DOOR_LOCK")
SEVERITIES = ("LOW", "MEDIUM", "HIGH", "EMERGENCY")
WORKER_SKILLS = ("PLUMBING", "ELECTRICAL", "LOCKSMITH")
IDEMPOTENCY_STATUSES = ("PENDING", "SUCCEEDED", "FAILED", "UNKNOWN")
APPOINTMENT_OUTCOME_CONSISTENCY = " ".join(
    (
        "(status IN ('CANCELLED', 'NO_SHOW')",
        "AND outcome_actor_type IS NOT NULL",
        "AND outcome_actor_id IS NOT NULL",
        "AND outcome_reason_code IS NOT NULL",
        "AND outcome_reason_text IS NOT NULL",
        "AND outcome_evidence IS NOT NULL",
        "AND outcome_occurred_at IS NOT NULL)",
        "OR (status NOT IN ('CANCELLED', 'NO_SHOW')",
        "AND outcome_actor_type IS NULL",
        "AND outcome_actor_id IS NULL",
        "AND outcome_reason_code IS NULL",
        "AND outcome_reason_text IS NULL",
        "AND outcome_evidence IS NULL",
        "AND outcome_occurred_at IS NULL)",
    )
)


def _in_values(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=100), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("role IN ('RESIDENT', 'OPERATOR')", name=op.f("ck_users_role_values")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("username", name=op.f("uq_users_username")),
    )
    op.create_table(
        "properties",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("community_name", sa.String(length=100), nullable=False),
        sa.Column("building_no", sa.String(length=30), nullable=False),
        sa.Column("unit_no", sa.String(length=30), nullable=False),
        sa.Column("room_no", sa.String(length=30), nullable=False),
        sa.Column("address_text", sa.String(length=500), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_properties")),
        sa.UniqueConstraint(
            "community_name",
            "building_no",
            "unit_no",
            "room_no",
            name=op.f("uq_properties_property_identity"),
        ),
    )
    op.create_table(
        "workers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("service_area", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workers")),
    )
    op.create_index(op.f("ix_workers_service_area"), "workers", ["service_area"])

    op.create_table(
        "resident_property_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resident_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_resident_property_relations_property_id_properties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resident_id"],
            ["users.id"],
            name=op.f("fk_resident_property_relations_resident_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resident_property_relations")),
        sa.UniqueConstraint(
            "resident_id",
            "property_id",
            name=op.f("uq_resident_property_relations_resident_property"),
        ),
    )
    op.create_index(
        op.f("ix_resident_property_relations_property_id"),
        "resident_property_relations",
        ["property_id"],
    )
    op.create_index(
        op.f("ix_resident_property_relations_resident_id"),
        "resident_property_relations",
        ["resident_id"],
    )
    op.create_table(
        "worker_skills",
        sa.Column("worker_id", sa.Uuid(), nullable=False),
        sa.Column("skill_type", sa.String(length=11), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_values("skill_type", WORKER_SKILLS),
            name=op.f("ck_worker_skills_worker_skill_type_values"),
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["workers.id"],
            name=op.f("fk_worker_skills_worker_id_workers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("worker_id", "skill_type", name=op.f("pk_worker_skills")),
    )
    op.create_table(
        "worker_availability",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("worker_id", sa.Uuid(), nullable=False),
        sa.Column("available_range", postgresql.TSTZRANGE(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "NOT isempty(available_range)",
            name=op.f("ck_worker_availability_range_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["workers.id"],
            name=op.f("fk_worker_availability_worker_id_workers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_availability")),
        sa.UniqueConstraint(
            "worker_id",
            "available_range",
            name=op.f("uq_worker_availability_worker_available_range"),
        ),
    )
    op.create_index(
        op.f("ix_worker_availability_worker_id"),
        "worker_availability",
        ["worker_id"],
    )

    op.create_table(
        "repair_tickets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resident_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("issue_category", sa.String(length=11), nullable=False),
        sa.Column("issue_location", sa.String(length=255), nullable=False),
        sa.Column("issue_description", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(length=9), nullable=False),
        sa.Column("status", sa.String(length=18), server_default="OPEN", nullable=False),
        sa.Column("escalated_from_status", sa.String(length=18), nullable=True),
        sa.Column("rework_count", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            _in_values("issue_category", ISSUE_CATEGORIES),
            name=op.f("ck_repair_tickets_issue_category_values"),
        ),
        sa.CheckConstraint(
            _in_values("severity", SEVERITIES),
            name=op.f("ck_repair_tickets_severity_values"),
        ),
        sa.CheckConstraint(
            _in_values("status", TICKET_STATUSES),
            name=op.f("ck_repair_tickets_ticket_status_values"),
        ),
        sa.CheckConstraint(
            "escalated_from_status IS NULL OR "
            + _in_values("escalated_from_status", TICKET_STATUSES),
            name=op.f("ck_repair_tickets_ticket_escalated_from_status_values"),
        ),
        sa.CheckConstraint(
            "rework_count >= 0", name=op.f("ck_repair_tickets_rework_count_nonnegative")
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_repair_tickets_version_positive")),
        sa.CheckConstraint(
            "(status = 'ESCALATED' AND escalated_from_status IS NOT NULL) OR "
            "(status <> 'ESCALATED' AND escalated_from_status IS NULL)",
            name=op.f("ck_repair_tickets_escalation_prior_consistent"),
        ),
        sa.CheckConstraint(
            "escalated_from_status IS NULL OR escalated_from_status IN "
            "('OPEN', 'SCHEDULED', 'IN_PROGRESS', 'PENDING_ACCEPTANCE', 'REWORK_REQUIRED')",
            name=op.f("ck_repair_tickets_escalation_prior_values"),
        ),
        sa.CheckConstraint(
            "length(btrim(issue_location)) > 0 AND length(btrim(issue_description)) > 0",
            name=op.f("ck_repair_tickets_issue_description_present"),
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_repair_tickets_property_id_properties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resident_id"],
            ["users.id"],
            name=op.f("fk_repair_tickets_resident_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repair_tickets")),
    )
    op.create_index(op.f("ix_repair_tickets_property_id"), "repair_tickets", ["property_id"])
    op.create_index(op.f("ix_repair_tickets_resident_id"), "repair_tickets", ["resident_id"])

    op.create_table(
        "ticket_status_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=18), nullable=True),
        sa.Column("to_status", sa.String(length=18), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("actor_type", sa.String(length=8), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("reason_text", sa.String(length=1000), nullable=True),
        sa.Column(
            "evidence", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("version_before", sa.BigInteger(), nullable=False),
        sa.Column("version_after", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"from_status IS NULL OR {_in_values('from_status', TICKET_STATUSES)}",
            name=op.f("ck_ticket_status_history_ticket_history_from_status_values"),
        ),
        sa.CheckConstraint(
            _in_values("to_status", TICKET_STATUSES),
            name=op.f("ck_ticket_status_history_ticket_history_to_status_values"),
        ),
        sa.CheckConstraint(
            _in_values("actor_type", ACTOR_TYPES),
            name=op.f("ck_ticket_status_history_ticket_history_actor_type_values"),
        ),
        sa.CheckConstraint(
            "version_before >= 0", name=op.f("ck_ticket_status_history_version_before_nonnegative")
        ),
        sa.CheckConstraint(
            "version_after = version_before + 1", name=op.f("ck_ticket_status_history_version_step")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["repair_tickets.id"],
            name=op.f("fk_ticket_status_history_ticket_id_repair_tickets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ticket_status_history")),
    )
    op.create_index(
        op.f("ix_ticket_status_history_ticket_id"), "ticket_status_history", ["ticket_id"]
    )
    op.create_index(
        op.f("ix_ticket_status_history_trace_id"), "ticket_status_history", ["trace_id"]
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("worker_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=14), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="BOOKED", nullable=False),
        sa.Column("scheduled_range", postgresql.TSTZRANGE(), nullable=False),
        sa.Column("supersedes_appointment_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("outcome_actor_type", sa.String(length=8), nullable=True),
        sa.Column("outcome_actor_id", sa.String(length=64), nullable=True),
        sa.Column("outcome_reason_code", sa.String(length=80), nullable=True),
        sa.Column("outcome_reason_text", sa.String(length=1000), nullable=True),
        sa.Column("outcome_evidence", postgresql.JSONB(), nullable=True),
        sa.Column("outcome_occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_values("purpose", APPOINTMENT_PURPOSES),
            name=op.f("ck_appointments_appointment_purpose_values"),
        ),
        sa.CheckConstraint(
            _in_values("status", APPOINTMENT_STATUSES),
            name=op.f("ck_appointments_appointment_status_values"),
        ),
        sa.CheckConstraint(
            f"outcome_actor_type IS NULL OR {_in_values('outcome_actor_type', ACTOR_TYPES)}",
            name=op.f("ck_appointments_appointment_outcome_actor_type_values"),
        ),
        sa.CheckConstraint(
            "NOT isempty(scheduled_range)", name=op.f("ck_appointments_scheduled_range_nonempty")
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_appointments_version_positive")),
        sa.CheckConstraint(
            "supersedes_appointment_id IS NULL OR supersedes_appointment_id <> id",
            name=op.f("ck_appointments_not_self_superseding"),
        ),
        sa.CheckConstraint(
            APPOINTMENT_OUTCOME_CONSISTENCY,
            name=op.f("ck_appointments_terminal_outcome_metadata_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_appointment_id"],
            ["appointments.id"],
            name=op.f("fk_appointments_supersedes_appointment_id_appointments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["repair_tickets.id"],
            name=op.f("fk_appointments_ticket_id_repair_tickets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["worker_id"],
            ["workers.id"],
            name=op.f("fk_appointments_worker_id_workers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointments")),
        sa.UniqueConstraint(
            "supersedes_appointment_id", name=op.f("uq_appointments_supersedes_appointment_id")
        ),
        postgresql.ExcludeConstraint(
            ("worker_id", "="),
            ("scheduled_range", "&&"),
            where=sa.text("status = 'BOOKED'"),
            using="gist",
            name=op.f("ex_appointments_worker_booked_overlap"),
        ),
    )
    op.create_index(op.f("ix_appointments_ticket_id"), "appointments", ["ticket_id"])
    op.create_index(op.f("ix_appointments_worker_id"), "appointments", ["worker_id"])
    op.create_index(
        "uq_appointments_ticket_booked",
        "appointments",
        ["ticket_id"],
        unique=True,
        postgresql_where=sa.text("status = 'BOOKED'"),
    )

    op.create_table(
        "appointment_status_history",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(length=10), nullable=True),
        sa.Column("to_status", sa.String(length=10), nullable=False),
        sa.Column("actor_type", sa.String(length=8), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("reason_text", sa.String(length=1000), nullable=True),
        sa.Column(
            "evidence", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version_before", sa.BigInteger(), nullable=False),
        sa.Column("version_after", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"from_status IS NULL OR {_in_values('from_status', APPOINTMENT_STATUSES)}",
            name=op.f("ck_appointment_status_history_appointment_history_from_status_values"),
        ),
        sa.CheckConstraint(
            _in_values("to_status", APPOINTMENT_STATUSES),
            name=op.f("ck_appointment_status_history_appointment_history_to_status_values"),
        ),
        sa.CheckConstraint(
            _in_values("actor_type", ACTOR_TYPES),
            name=op.f("ck_appointment_status_history_appointment_history_actor_type_values"),
        ),
        sa.CheckConstraint(
            "version_before >= 0",
            name=op.f("ck_appointment_status_history_version_before_nonnegative"),
        ),
        sa.CheckConstraint(
            "version_after = version_before + 1",
            name=op.f("ck_appointment_status_history_version_step"),
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name=op.f("fk_appointment_status_history_appointment_id_appointments"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_status_history")),
    )
    op.create_index(
        op.f("ix_appointment_status_history_appointment_id"),
        "appointment_status_history",
        ["appointment_id"],
    )

    op.create_table(
        "worker_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("appointment_id", sa.Uuid(), nullable=False),
        sa.Column("subject_worker_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=18), nullable=False),
        sa.Column("actor_type", sa.String(length=8), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("external_event_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            _in_values("event_type", WORKER_EVENT_TYPES),
            name=op.f("ck_worker_events_worker_event_type_values"),
        ),
        sa.CheckConstraint(
            _in_values("actor_type", ACTOR_TYPES),
            name=op.f("ck_worker_events_worker_event_actor_type_values"),
        ),
        sa.CheckConstraint("sequence_no > 0", name=op.f("ck_worker_events_sequence_positive")),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name=op.f("fk_worker_events_appointment_id_appointments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["subject_worker_id"],
            ["workers.id"],
            name=op.f("fk_worker_events_subject_worker_id_workers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_worker_events")),
        sa.UniqueConstraint(
            "appointment_id", "sequence_no", name=op.f("uq_worker_events_appointment_sequence")
        ),
        sa.UniqueConstraint("external_event_key", name=op.f("uq_worker_events_external_event_key")),
    )
    op.create_index(op.f("ix_worker_events_appointment_id"), "worker_events", ["appointment_id"])
    op.create_index(
        op.f("ix_worker_events_subject_worker_id"), "worker_events", ["subject_worker_id"]
    )

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=100), nullable=False),
        sa.Column("actor_type", sa.String(length=8), nullable=False),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "execution_status", sa.String(length=9), server_default="PENDING", nullable=False
        ),
        sa.Column("resource_type", sa.String(length=80), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("response_payload", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            _in_values("actor_type", ACTOR_TYPES),
            name=op.f("ck_idempotency_records_idempotency_actor_type_values"),
        ),
        sa.CheckConstraint(
            _in_values("execution_status", IDEMPOTENCY_STATUSES),
            name=op.f("ck_idempotency_records_idempotency_execution_status_values"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_records")),
        sa.UniqueConstraint(
            "scope",
            "actor_type",
            "actor_id",
            "idempotency_key",
            name=op.f("uq_idempotency_records_operation_actor_key"),
        ),
    )
    op.create_index(
        op.f("ix_idempotency_records_expires_at"), "idempotency_records", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_idempotency_records_expires_at"), table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index(op.f("ix_worker_events_subject_worker_id"), table_name="worker_events")
    op.drop_index(op.f("ix_worker_events_appointment_id"), table_name="worker_events")
    op.drop_table("worker_events")
    op.drop_index(
        op.f("ix_appointment_status_history_appointment_id"),
        table_name="appointment_status_history",
    )
    op.drop_table("appointment_status_history")
    op.drop_index("uq_appointments_ticket_booked", table_name="appointments")
    op.drop_index(op.f("ix_appointments_worker_id"), table_name="appointments")
    op.drop_index(op.f("ix_appointments_ticket_id"), table_name="appointments")
    op.drop_table("appointments")
    op.drop_index(op.f("ix_ticket_status_history_trace_id"), table_name="ticket_status_history")
    op.drop_index(op.f("ix_ticket_status_history_ticket_id"), table_name="ticket_status_history")
    op.drop_table("ticket_status_history")
    op.drop_index(op.f("ix_repair_tickets_resident_id"), table_name="repair_tickets")
    op.drop_index(op.f("ix_repair_tickets_property_id"), table_name="repair_tickets")
    op.drop_table("repair_tickets")
    op.drop_index(op.f("ix_worker_availability_worker_id"), table_name="worker_availability")
    op.drop_table("worker_availability")
    op.drop_table("worker_skills")
    op.drop_index(
        op.f("ix_resident_property_relations_resident_id"), table_name="resident_property_relations"
    )
    op.drop_index(
        op.f("ix_resident_property_relations_property_id"), table_name="resident_property_relations"
    )
    op.drop_table("resident_property_relations")
    op.drop_index(op.f("ix_workers_service_area"), table_name="workers")
    op.drop_table("workers")
    op.drop_table("properties")
    op.drop_table("users")

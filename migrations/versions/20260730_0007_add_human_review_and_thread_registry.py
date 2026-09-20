"""add durable agent results, human review, and thread registry

Revision ID: 20260730_0007
Revises: 20260723_0006
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0007"
down_revision: str | None = "20260723_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _values(*items: str) -> str:
    return ",".join(f"'{item}'" for item in items)


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("message_outcome", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column(
            "required_user_action",
            sa.String(length=24),
            nullable=False,
            server_default=sa.text("'NONE'"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE agent_runs SET message_outcome = CASE "
            "WHEN status IN ('FAILED','FAILED_SAFE') THEN 'FAILED' "
            "WHEN status <> 'RUNNING' THEN 'COMPLETED' ELSE NULL END"
        )
    )
    op.create_check_constraint(
        op.f("ck_agent_runs_agent_run_message_outcome_values"),
        "agent_runs",
        f"message_outcome IS NULL OR message_outcome IN "
        f"({_values('COMPLETED', 'FAILED', 'ESCALATED')})",
    )
    op.create_check_constraint(
        op.f("ck_agent_runs_agent_run_required_user_action_values"),
        "agent_runs",
        "required_user_action IN "
        f"({_values('NONE', 'PROVIDE_DETAILS', 'SELECT_SLOT', 'CONFIRM_ACTION')},"
        f"{_values('CONTACT_OPERATOR', 'RETRY')})",
    )
    op.create_check_constraint(
        op.f("ck_agent_runs_message_outcome_matches_status"),
        "agent_runs",
        "(status = 'RUNNING' AND message_outcome IS NULL) OR "
        "(status <> 'RUNNING' AND message_outcome IS NOT NULL)",
    )
    op.create_index(
        "uq_agent_trace_events_message_terminal_per_run",
        "agent_trace_events",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text(
            "event_type IN ('message.completed','message.failed','message.escalated')"
        ),
    )

    op.create_table(
        "agent_thread_records",
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("resident_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=True),
        sa.Column("property_resolution_status", sa.String(length=20), nullable=False),
        sa.Column("lifecycle_status", sa.String(length=20), nullable=False),
        sa.Column("display_title", sa.String(length=200), nullable=True),
        sa.Column("last_activity_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_by_actor_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            f"property_resolution_status IN ({_values('UNRESOLVED', 'RESOLVED', 'DENIED')})",
            name=op.f("ck_agent_thread_records_agent_thread_property_resolution_status_values"),
        ),
        sa.CheckConstraint(
            f"lifecycle_status IN ({_values('ACTIVE', 'ARCHIVED')})",
            name=op.f("ck_agent_thread_records_agent_thread_lifecycle_status_values"),
        ),
        sa.CheckConstraint("version > 0", name=op.f("ck_agent_thread_records_version_positive")),
        sa.CheckConstraint(
            "(lifecycle_status = 'ACTIVE' AND archived_at IS NULL) OR "
            "(lifecycle_status = 'ARCHIVED' AND archived_at IS NOT NULL)",
            name=op.f("ck_agent_thread_records_archive_time_matches_status"),
        ),
        sa.CheckConstraint(
            "(property_resolution_status = 'RESOLVED' AND property_id IS NOT NULL) OR "
            "(property_resolution_status <> 'RESOLVED')",
            name=op.f("ck_agent_thread_records_resolved_property_present"),
        ),
        sa.ForeignKeyConstraint(
            ["resident_id"],
            ["users.id"],
            name=op.f("fk_agent_thread_records_resident_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_agent_thread_records_property_id_properties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["archived_by_actor_id"],
            ["users.id"],
            name=op.f("fk_agent_thread_records_archived_by_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("thread_id", name=op.f("pk_agent_thread_records")),
    )
    op.create_index(
        op.f("ix_agent_thread_records_resident_id"),
        "agent_thread_records",
        ["resident_id"],
    )
    op.create_index(
        op.f("ix_agent_thread_records_property_id"),
        "agent_thread_records",
        ["property_id"],
    )
    op.create_index(
        "ix_agent_thread_records_resident_lifecycle_activity",
        "agent_thread_records",
        ["resident_id", "lifecycle_status", sa.text("last_activity_at DESC")],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO agent_thread_records (
                thread_id, resident_id, property_id, property_resolution_status,
                lifecycle_status, display_title, last_activity_at, created_at,
                updated_at, archived_at, archived_by_actor_id, version
            )
            SELECT
                thread_id,
                min(user_id::text)::uuid,
                (array_agg(property_id ORDER BY started_at)
                    FILTER (WHERE property_id IS NOT NULL))[1],
                CASE
                    WHEN count(property_id) FILTER (WHERE property_id IS NOT NULL) > 0
                    THEN 'RESOLVED' ELSE 'UNRESOLVED'
                END,
                'ACTIVE',
                NULL,
                max(started_at),
                min(started_at),
                max(started_at),
                NULL,
                NULL,
                1
            FROM agent_runs
            WHERE actor_type = 'RESIDENT'
              AND thread_id IS NOT NULL
              AND user_id IS NOT NULL
              AND EXISTS (SELECT 1 FROM users WHERE users.id = agent_runs.user_id)
            GROUP BY thread_id
            HAVING count(DISTINCT user_id) = 1
               AND count(DISTINCT property_id) FILTER (WHERE property_id IS NOT NULL) <= 1
            """
        )
    )

    op.create_table(
        "agent_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"role IN ({_values('USER', 'ASSISTANT')})",
            name=op.f("ck_agent_messages_agent_message_role_values"),
        ),
        sa.CheckConstraint(
            "sequence_no > 0",
            name=op.f("ck_agent_messages_sequence_positive"),
        ),
        sa.CheckConstraint(
            "length(btrim(content)) > 0",
            name=op.f("ck_agent_messages_content_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["agent_thread_records.thread_id"],
            name=op.f("fk_agent_messages_thread_id_agent_thread_records"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_agent_messages_run_id_agent_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_messages")),
        sa.UniqueConstraint("message_id", name=op.f("uq_agent_messages_message_id")),
        sa.UniqueConstraint(
            "thread_id",
            "sequence_no",
            name=op.f("uq_agent_messages_thread_sequence"),
        ),
        sa.UniqueConstraint("run_id", "role", name=op.f("uq_agent_messages_run_role")),
    )
    op.create_index(op.f("ix_agent_messages_run_id"), "agent_messages", ["run_id"])
    op.create_index(
        "ix_agent_messages_thread_created",
        "agent_messages",
        ["thread_id", "created_at"],
    )

    op.create_table(
        "human_review_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("resident_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=True),
        sa.Column("ticket_id", sa.Uuid(), nullable=True),
        sa.Column("source_run_id", sa.Uuid(), nullable=True),
        sa.Column("intent_version", sa.Integer(), nullable=False),
        sa.Column("failure_stage", sa.String(length=40), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column("safety_level", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("assigned_operator_id", sa.Uuid(), nullable=True),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_code", sa.String(length=80), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "failure_stage IN "
            f"({_values('INTERPRETATION', 'PROPERTY_AUTHORIZATION', 'SAFETY_REVIEW')},"
            f"{_values('POLICY_REVIEW', 'DUPLICATE_CHECK', 'SCHEDULING')},"
            f"{_values('MUTATION_RECONCILIATION', 'UNSUPPORTED_REQUEST')})",
            name=op.f("ck_human_review_cases_human_review_failure_stage_values"),
        ),
        sa.CheckConstraint(
            f"safety_level IN ({_values('STANDARD', 'ELEVATED', 'EMERGENCY')})",
            name=op.f("ck_human_review_cases_human_review_safety_level_values"),
        ),
        sa.CheckConstraint(
            f"status IN ({_values('OPEN', 'CLAIMED', 'RESOLVED', 'DISMISSED')})",
            name=op.f("ck_human_review_cases_human_review_status_values"),
        ),
        sa.CheckConstraint(
            "intent_version > 0",
            name=op.f("ck_human_review_cases_intent_version_positive"),
        ),
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_human_review_cases_version_positive"),
        ),
        sa.CheckConstraint(
            "priority BETWEEN 1 AND 100",
            name=op.f("ck_human_review_cases_priority_range"),
        ),
        sa.CheckConstraint(
            "length(btrim(summary)) > 0",
            name=op.f("ck_human_review_cases_summary_not_blank"),
        ),
        sa.CheckConstraint(
            "(status = 'CLAIMED' AND assigned_operator_id IS NOT NULL "
            "AND claimed_at IS NOT NULL) OR (status <> 'CLAIMED')",
            name=op.f("ck_human_review_cases_claim_fields_match_status"),
        ),
        sa.CheckConstraint(
            "(status IN ('RESOLVED','DISMISSED') AND resolved_at IS NOT NULL) OR "
            "(status NOT IN ('RESOLVED','DISMISSED') AND resolved_at IS NULL)",
            name=op.f("ck_human_review_cases_resolution_time_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["agent_thread_records.thread_id"],
            name=op.f("fk_human_review_cases_thread_id_agent_thread_records"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resident_id"],
            ["users.id"],
            name=op.f("fk_human_review_cases_resident_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["property_id"],
            ["properties.id"],
            name=op.f("fk_human_review_cases_property_id_properties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["repair_tickets.id"],
            name=op.f("fk_human_review_cases_ticket_id_repair_tickets"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_human_review_cases_source_run_id_agent_runs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_operator_id"],
            ["users.id"],
            name=op.f("fk_human_review_cases_assigned_operator_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_human_review_cases")),
    )
    for column in ("resident_id", "property_id", "ticket_id", "source_run_id"):
        op.create_index(op.f(f"ix_human_review_cases_{column}"), "human_review_cases", [column])
    op.create_index(
        "uq_human_review_cases_active_dedupe",
        "human_review_cases",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN','CLAIMED')"),
    )
    op.create_index(
        "ix_human_review_cases_queue",
        "human_review_cases",
        ["status", "priority", "created_at"],
    )
    op.create_index(
        "ix_human_review_cases_thread",
        "human_review_cases",
        ["thread_id", "created_at"],
    )

    op.create_table(
        "human_review_case_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("action", sa.String(length=40), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("version_before", sa.Integer(), nullable=False),
        sa.Column("version_after", sa.Integer(), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN "
            f"({_values('OPEN', 'CLAIMED', 'RESOLVED', 'DISMISSED')})",
            name=op.f("ck_human_review_case_events_human_review_event_from_status_values"),
        ),
        sa.CheckConstraint(
            f"to_status IN ({_values('OPEN', 'CLAIMED', 'RESOLVED', 'DISMISSED')})",
            name=op.f("ck_human_review_case_events_human_review_event_to_status_values"),
        ),
        sa.CheckConstraint(
            f"actor_type IN ({_values('RESIDENT', 'OPERATOR', 'WORKER', 'SYSTEM')})",
            name=op.f("ck_human_review_case_events_human_review_event_actor_type_values"),
        ),
        sa.CheckConstraint(
            "sequence_no > 0",
            name=op.f("ck_human_review_case_events_sequence_positive"),
        ),
        sa.CheckConstraint(
            "version_before >= 0",
            name=op.f("ck_human_review_case_events_version_before_nonnegative"),
        ),
        sa.CheckConstraint(
            "version_after > version_before",
            name=op.f("ck_human_review_case_events_version_increases"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["human_review_cases.id"],
            name=op.f("fk_human_review_case_events_case_id_human_review_cases"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_human_review_case_events")),
        sa.UniqueConstraint(
            "case_id",
            "sequence_no",
            name=op.f("uq_human_review_case_events_case_sequence"),
        ),
    )
    op.create_index(
        op.f("ix_human_review_case_events_case_id"),
        "human_review_case_events",
        ["case_id"],
    )
    op.create_index(
        op.f("ix_human_review_case_events_trace_id"),
        "human_review_case_events",
        ["trace_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "uq_agent_trace_events_message_terminal_per_run",
        table_name="agent_trace_events",
    )
    op.drop_index(
        op.f("ix_human_review_case_events_trace_id"),
        table_name="human_review_case_events",
    )
    op.drop_index(
        op.f("ix_human_review_case_events_case_id"),
        table_name="human_review_case_events",
    )
    op.drop_table("human_review_case_events")

    op.drop_index("ix_human_review_cases_thread", table_name="human_review_cases")
    op.drop_index("ix_human_review_cases_queue", table_name="human_review_cases")
    op.drop_index("uq_human_review_cases_active_dedupe", table_name="human_review_cases")
    for column in ("source_run_id", "ticket_id", "property_id", "resident_id"):
        op.drop_index(op.f(f"ix_human_review_cases_{column}"), table_name="human_review_cases")
    op.drop_table("human_review_cases")

    op.drop_index("ix_agent_messages_thread_created", table_name="agent_messages")
    op.drop_index(op.f("ix_agent_messages_run_id"), table_name="agent_messages")
    op.drop_table("agent_messages")

    op.drop_index(
        "ix_agent_thread_records_resident_lifecycle_activity",
        table_name="agent_thread_records",
    )
    op.drop_index(
        op.f("ix_agent_thread_records_property_id"),
        table_name="agent_thread_records",
    )
    op.drop_index(
        op.f("ix_agent_thread_records_resident_id"),
        table_name="agent_thread_records",
    )
    op.drop_table("agent_thread_records")

    op.drop_constraint(
        op.f("ck_agent_runs_message_outcome_matches_status"),
        "agent_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_runs_agent_run_required_user_action_values"),
        "agent_runs",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_runs_agent_run_message_outcome_values"),
        "agent_runs",
        type_="check",
    )
    op.drop_column("agent_runs", "required_user_action")
    op.drop_column("agent_runs", "message_outcome")

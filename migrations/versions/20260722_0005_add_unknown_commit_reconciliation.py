"""Add UNKNOWN_COMMIT reconciliation control plane.

Revision ID: 20260722_0005
Revises: 20260721_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260722_0005"
down_revision: str | Sequence[str] | None = "20260721_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("idempotency_records", sa.Column("operation_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_idempotency_records_operation_id", "idempotency_records", ["operation_id"], unique=True
    )
    op.execute(
        sa.text(
            """DO $$ DECLARE n text; BEGIN
            SELECT conname INTO n FROM pg_constraint
            WHERE conrelid='agent_trace_events'::regclass AND contype='c'
              AND pg_get_constraintdef(oid) LIKE '%source%API%AGENT%MCP%DOMAIN%OUTBOX%';
            IF n IS NOT NULL THEN
              EXECUTE format('ALTER TABLE agent_trace_events DROP CONSTRAINT %I', n);
            END IF;
            END $$"""
        )
    )
    op.alter_column(
        "agent_trace_events",
        "source",
        existing_type=sa.String(7),
        type_=sa.String(14),
        existing_nullable=False,
    )
    op.execute(
        sa.text(
            "ALTER TABLE agent_trace_events ADD CONSTRAINT ck_agent_trace_source_values "
            "CHECK (source IN ('API','AGENT','MCP','DOMAIN','OUTBOX','RECONCILIATION'))"
        )
    )
    op.create_table(
        "operation_reconciliation_cases",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("case_key", sa.String(64), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=True),
        sa.Column("original_run_id", sa.Uuid(), nullable=False),
        sa.Column("original_trace_id", sa.Uuid(), nullable=False),
        sa.Column("operation_idempotency_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("actor_type", sa.String(24), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("target_entity_type", sa.String(40), nullable=True),
        sa.Column("target_entity_id", sa.Uuid(), nullable=True),
        sa.Column("expected_entity_version", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(24), server_default=sa.text("'PENDING'"), nullable=False),
        sa.Column("last_evidence_status", sa.String(20), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_code", sa.String(80), nullable=True),
        sa.Column("safe_result", postgresql.JSONB(), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_operation_reconciliation_cases"),
        sa.UniqueConstraint("case_key", name="uq_operation_reconciliation_cases_case_key"),
        sa.UniqueConstraint("operation_id", name="uq_operation_reconciliation_cases_operation_id"),
        sa.CheckConstraint(
            "action IN ('CREATE_TICKET','BOOK_APPOINTMENT',"
            "'RESCHEDULE_APPOINTMENT','ESCALATE_TO_OPERATOR')",
            name="ck_operation_reconciliation_cases_reconciliation_action_values",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','RESOLVED_COMMITTED',"
            "'RESOLVED_NOT_COMMITTED','MANUAL_REVIEW')",
            name="ck_operation_reconciliation_cases_reconciliation_status_values",
        ),
        sa.CheckConstraint(
            "last_evidence_status IS NULL OR last_evidence_status IN "
            "('COMMITTED','NOT_COMMITTED','INCONSISTENT')",
            name="ck_operation_reconciliation_cases_reconciliation_evidence_status_values",
        ),
        sa.CheckConstraint(
            "actor_type IN ('RESIDENT','OPERATOR','WORKER','SYSTEM')",
            name="ck_operation_reconciliation_cases_reconciliation_actor_type_values",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_operation_reconciliation_cases_attempt_count_nonnegative"
        ),
        sa.CheckConstraint(
            "(status = 'PROCESSING' AND claimed_by IS NOT NULL AND claim_token IS NOT NULL "
            "AND claim_expires_at IS NOT NULL) OR (status <> 'PROCESSING' "
            "AND claimed_by IS NULL AND claim_token IS NULL AND claim_expires_at IS NULL)",
            name="ck_operation_reconciliation_cases_claim_matches_status",
        ),
        sa.CheckConstraint(
            "(status IN ('RESOLVED_COMMITTED','RESOLVED_NOT_COMMITTED','MANUAL_REVIEW') "
            "AND resolved_at IS NOT NULL) OR "
            "(status IN ('PENDING','PROCESSING') AND resolved_at IS NULL)",
            name="ck_operation_reconciliation_cases_resolution_matches_status",
        ),
    )
    for column in (
        "operation_id",
        "thread_id",
        "original_run_id",
        "property_id",
        "target_entity_id",
    ):
        op.create_index(
            f"ix_operation_reconciliation_cases_{column}",
            "operation_reconciliation_cases",
            [column],
        )
    op.create_index(
        "ix_reconciliation_claim",
        "operation_reconciliation_cases",
        ["status", "available_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("operation_reconciliation_cases")
    op.execute(
        sa.text("ALTER TABLE agent_trace_events DROP CONSTRAINT ck_agent_trace_source_values")
    )
    # Reconciliation audit events have no representation before revision 0005.
    op.execute(sa.text("DELETE FROM agent_trace_events WHERE source = 'RECONCILIATION'"))
    op.alter_column(
        "agent_trace_events",
        "source",
        existing_type=sa.String(14),
        type_=sa.String(7),
        existing_nullable=False,
    )
    op.execute(
        sa.text(
            "ALTER TABLE agent_trace_events ADD CONSTRAINT "
            "ck_agent_trace_events_agent_trace_source_values "
            "CHECK (source IN ('API','AGENT','MCP','DOMAIN','OUTBOX'))"
        )
    )
    op.drop_index("ix_idempotency_records_operation_id", table_name="idempotency_records")
    op.drop_column("idempotency_records", "operation_id")

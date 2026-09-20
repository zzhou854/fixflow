"""Add transactional outbox and persistent Agent trace runtime.

Revision ID: 20260721_0004
Revises: 20260720_0003
Create Date: 2026-07-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260721_0004"
down_revision: str | Sequence[str] | None = "20260720_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUN_TRIGGERS = ("THREAD_CREATED", "MESSAGE", "RESUME", "OPERATOR_ACTION")
_RUN_STATUSES = ("RUNNING", "INTERRUPTED", "COMPLETED", "FAILED_SAFE", "FAILED")
_TRACE_SOURCES = ("API", "AGENT", "MCP", "DOMAIN", "OUTBOX")
_OUTBOX_STATUSES = ("PENDING", "PROCESSING", "DISPATCHED", "DEAD_LETTER")


def _values(values: tuple[str, ...]) -> str:
    return str(values).replace('"', "'")


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(15), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("actor_type", sa.String(24), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("property_id", sa.Uuid(), nullable=True),
        sa.Column("initial_intent_version", sa.Integer(), nullable=True),
        sa.Column("final_intent_version", sa.Integer(), nullable=True),
        sa.Column("last_sequence", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminal_event_type", sa.String(80), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
        sa.UniqueConstraint("trace_id", name=op.f("uq_agent_runs_trace_id")),
        sa.CheckConstraint(
            "trigger IN " + _values(_RUN_TRIGGERS),
            name=op.f("ck_agent_runs_agent_run_trigger_values"),
        ),
        sa.CheckConstraint(
            "status IN " + _values(_RUN_STATUSES),
            name=op.f("ck_agent_runs_agent_run_status_values"),
        ),
        sa.CheckConstraint(
            "actor_type IN ('RESIDENT','OPERATOR','WORKER','SYSTEM')",
            name=op.f("ck_agent_runs_actor_type_values"),
        ),
        sa.CheckConstraint(
            "last_sequence >= 0", name=op.f("ck_agent_runs_last_sequence_nonnegative")
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND finished_at IS NULL) OR "
            "(status <> 'RUNNING' AND finished_at IS NOT NULL)",
            name=op.f("ck_agent_runs_finish_time_matches_status"),
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND terminal_event_type IS NULL) OR "
            "(status = 'INTERRUPTED' AND terminal_event_type = 'run_interrupted') OR "
            "(status = 'COMPLETED' AND terminal_event_type = 'run_completed') OR "
            "(status = 'FAILED_SAFE' AND terminal_event_type = 'run_failed_safe') OR "
            "(status = 'FAILED' AND terminal_event_type = 'run_failed')",
            name=op.f("ck_agent_runs_terminal_event_matches_status"),
        ),
    )
    op.create_index(op.f("ix_agent_runs_thread_id"), "agent_runs", ["thread_id"])
    op.create_index(op.f("ix_agent_runs_trace_id"), "agent_runs", ["trace_id"])

    op.create_table(
        "agent_trace_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(128), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("thread_id", sa.Uuid(), nullable=True),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.BigInteger(), nullable=True),
        sa.Column("source", sa.String(7), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("node_name", sa.String(80), nullable=True),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_agent_trace_events_run_id_agent_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_trace_events")),
        sa.UniqueConstraint("event_key", name="uq_agent_trace_events_event_key"),
        sa.UniqueConstraint("run_id", "sequence_number", name="uq_agent_trace_events_run_sequence"),
        sa.CheckConstraint(
            "source IN " + _values(_TRACE_SOURCES),
            name=op.f("ck_agent_trace_events_agent_trace_source_values"),
        ),
        sa.CheckConstraint(
            "(run_id IS NULL AND sequence_number IS NULL) OR "
            "(run_id IS NOT NULL AND sequence_number > 0)",
            name=op.f("ck_agent_trace_events_run_sequence_pair_valid"),
        ),
    )
    for column in ("run_id", "thread_id", "trace_id", "event_type", "operation_id"):
        op.create_index(op.f(f"ix_agent_trace_events_{column}"), "agent_trace_events", [column])
    op.create_index(
        "uq_agent_trace_events_lifecycle_terminal_per_run",
        "agent_trace_events",
        ["run_id"],
        unique=True,
        postgresql_where=sa.text(
            "event_type IN ('run_interrupted','run_completed','run_failed_safe','run_failed')"
        ),
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_key", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("aggregate_type", sa.String(40), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_version", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("trace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("thread_id", sa.Uuid(), nullable=True),
        sa.Column("actor_type", sa.String(24), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(11), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.String(128), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("event_key", name="uq_outbox_events_event_key"),
        sa.CheckConstraint(
            "status IN " + _values(_OUTBOX_STATUSES),
            name=op.f("ck_outbox_events_outbox_status_values"),
        ),
        sa.CheckConstraint(
            "aggregate_version > 0", name=op.f("ck_outbox_events_aggregate_version_positive")
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_outbox_events_attempt_count_nonnegative")
        ),
        sa.CheckConstraint(
            "actor_type IN ('RESIDENT','OPERATOR','WORKER','SYSTEM')",
            name=op.f("ck_outbox_events_actor_type_values"),
        ),
        sa.CheckConstraint(
            "(status = 'PROCESSING' AND claimed_by IS NOT NULL AND claim_token IS NOT NULL "
            "AND claim_expires_at IS NOT NULL) OR "
            "(status <> 'PROCESSING' AND claimed_by IS NULL AND claim_token IS NULL "
            "AND claim_expires_at IS NULL)",
            name=op.f("ck_outbox_events_claim_matches_status"),
        ),
        sa.CheckConstraint(
            "(status = 'DISPATCHED' AND dispatched_at IS NOT NULL) OR "
            "(status <> 'DISPATCHED' AND dispatched_at IS NULL)",
            name=op.f("ck_outbox_events_dispatch_time_matches_status"),
        ),
    )
    for column in ("aggregate_id", "operation_id", "trace_id", "run_id", "thread_id"):
        op.create_index(op.f(f"ix_outbox_events_{column}"), "outbox_events", [column])
    op.create_index(
        "ix_outbox_events_dispatch",
        "outbox_events",
        ["status", "available_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_table("agent_trace_events")
    op.drop_table("agent_runs")

"""add deterministic replay control plane

Revision ID: 20260723_0006
Revises: 20260722_0005
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260723_0006"
down_revision: str | None = "20260722_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _values(*items: str) -> str:
    return ",".join(f"'{item}'" for item in items)


def upgrade() -> None:
    op.execute(
        sa.text("ALTER TABLE agent_trace_events DROP CONSTRAINT ck_agent_trace_source_values")
    )
    op.alter_column(
        "agent_trace_events",
        "source",
        existing_type=sa.String(length=14),
        type_=sa.String(length=14),
        existing_nullable=False,
    )
    op.execute(
        sa.text(
            "ALTER TABLE agent_trace_events ADD CONSTRAINT ck_agent_trace_source_values "
            "CHECK (source IN "
            "('API','AGENT','MCP','DOMAIN','OUTBOX','RECONCILIATION','REPLAY'))"
        )
    )

    op.create_table(
        "agent_replay_bundles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bundle_id", sa.Uuid(), nullable=False),
        sa.Column("original_run_id", sa.Uuid(), nullable=False),
        sa.Column("thread_id", sa.Uuid(), nullable=True),
        sa.Column("original_trace_id", sa.Uuid(), nullable=False),
        sa.Column("trigger_type", sa.String(length=14), nullable=False),
        sa.Column("status", sa.String(length=11), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("graph_schema_version", sa.Integer(), nullable=False),
        sa.Column("runtime_revision", sa.String(length=128), nullable=False),
        sa.Column("start_state_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("input_envelope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "expected_result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("expected_route_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("expected_state_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bundle_checksum", sa.String(length=64), nullable=True),
        sa.Column("capture_error_code", sa.String(length=80), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_agent_replay_bundles_schema_version_positive",
        ),
        sa.CheckConstraint(
            "graph_schema_version > 0",
            name="ck_agent_replay_bundles_graph_schema_version_positive",
        ),
        sa.CheckConstraint(
            "step_count >= 0", name="ck_agent_replay_bundles_step_count_nonnegative"
        ),
        sa.CheckConstraint(
            "(status = 'CAPTURING' AND finalized_at IS NULL AND bundle_checksum IS NULL) OR "
            "(status <> 'CAPTURING' AND finalized_at IS NOT NULL)",
            name="ck_agent_replay_bundles_finalization_matches_status",
        ),
        sa.CheckConstraint(
            "status <> 'READY' OR "
            "(start_state_payload IS NOT NULL AND expected_result_payload IS NOT NULL "
            "AND expected_route_fingerprint IS NOT NULL "
            "AND expected_state_fingerprint IS NOT NULL AND bundle_checksum IS NOT NULL)",
            name="ck_agent_replay_bundles_ready_bundle_is_complete",
        ),
        sa.CheckConstraint(
            "trigger_type IN ("
            + _values("THREAD_CREATED", "MESSAGE", "RESUME", "OPERATOR_ACTION")
            + ")",
            name="ck_agent_replay_bundles_replay_trigger_type_values",
        ),
        sa.CheckConstraint(
            f"status IN ({_values('CAPTURING', 'READY', 'INCOMPLETE', 'INVALID', 'UNAVAILABLE')})",
            name="ck_agent_replay_bundles_replay_bundle_status_values",
        ),
        sa.ForeignKeyConstraint(["original_run_id"], ["agent_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bundle_id", name="uq_agent_replay_bundles_bundle_id"),
        sa.UniqueConstraint("original_run_id", name="uq_agent_replay_bundles_original_run"),
    )
    op.create_index(
        "ix_agent_replay_bundles_thread_id",
        "agent_replay_bundles",
        ["thread_id"],
    )
    op.create_index(
        "ix_agent_replay_bundles_original_trace_id",
        "agent_replay_bundles",
        ["original_trace_id"],
    )
    op.create_index(
        "ix_agent_replay_bundles_thread_captured",
        "agent_replay_bundles",
        ["thread_id", "captured_at"],
    )

    op.create_table(
        "agent_replay_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bundle_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("step_kind", sa.String(length=29), nullable=False),
        sa.Column("step_key", sa.String(length=160), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("response_schema", sa.String(length=160), nullable=False),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("step_checksum", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "sequence_number > 0", name="ck_agent_replay_steps_sequence_number_positive"
        ),
        sa.CheckConstraint(
            "step_kind IN ("
            + _values(
                "NODE_ENTERED",
                "INTERPRETATION_RESULT",
                "PROPERTY_AUTHORIZATION_RESULT",
                "POLICY_RESULT",
                "DUPLICATE_LOOKUP_RESULT",
                "TICKET_SNAPSHOT_RESULT",
                "SLOT_LOOKUP_RESULT",
                "MCP_MUTATION_RESULT",
                "OPERATOR_MUTATION_RESULT",
                "ROUTE_DECISION",
                "INTERRUPT_RESULT",
                "FINAL_STATE",
            )
            + ")",
            name="ck_agent_replay_steps_replay_step_kind_values",
        ),
        sa.ForeignKeyConstraint(
            ["bundle_id"], ["agent_replay_bundles.bundle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bundle_id", "sequence_number", name="uq_agent_replay_steps_bundle_sequence"
        ),
        sa.UniqueConstraint("bundle_id", "step_key", name="uq_agent_replay_steps_bundle_key"),
    )
    op.create_index(
        "ix_agent_replay_steps_bundle_sequence",
        "agent_replay_steps",
        ["bundle_id", "sequence_number"],
    )

    op.create_table(
        "agent_replay_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("bundle_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_actor_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("request_key_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=18), nullable=False),
        sa.Column("runtime_revision", sa.String(length=128), nullable=False),
        sa.Column("graph_schema_version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_route_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("actual_state_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("mismatch_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("comparison_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("recommendation", sa.String(length=80), nullable=True),
        sa.Column("safe_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint(
            "graph_schema_version > 0",
            name="ck_agent_replay_executions_graph_schema_version_positive",
        ),
        sa.CheckConstraint(
            "mismatch_count >= 0",
            name="ck_agent_replay_executions_mismatch_count_nonnegative",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND completed_at IS NULL) OR "
            "(status <> 'RUNNING' AND completed_at IS NOT NULL)",
            name="ck_agent_replay_executions_completion_matches_status",
        ),
        sa.CheckConstraint(
            "(status = 'RUNNING' AND comparison_summary IS NULL AND recommendation IS NULL) OR "
            "(status <> 'RUNNING' AND comparison_summary IS NOT NULL "
            "AND recommendation IS NOT NULL)",
            name="ck_agent_replay_executions_result_matches_status",
        ),
        sa.CheckConstraint(
            "status IN ("
            + _values(
                "RUNNING",
                "PASSED",
                "DIVERGED",
                "INCOMPLETE",
                "UNSUPPORTED_SCHEMA",
                "FAILED_SAFE",
            )
            + ")",
            name="ck_agent_replay_executions_replay_execution_status_values",
        ),
        sa.ForeignKeyConstraint(
            ["bundle_id"], ["agent_replay_bundles.bundle_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", name="uq_agent_replay_executions_execution_id"),
        sa.UniqueConstraint(
            "requested_by_actor_id",
            "request_key_fingerprint",
            name="uq_agent_replay_executions_actor_request_key",
        ),
    )
    op.create_index(
        "ix_agent_replay_executions_bundle_started",
        "agent_replay_executions",
        ["bundle_id", "started_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_replay_executions_bundle_started",
        table_name="agent_replay_executions",
    )
    op.drop_table("agent_replay_executions")
    op.drop_index("ix_agent_replay_steps_bundle_sequence", table_name="agent_replay_steps")
    op.drop_table("agent_replay_steps")
    op.drop_index("ix_agent_replay_bundles_thread_captured", table_name="agent_replay_bundles")
    op.drop_index("ix_agent_replay_bundles_original_trace_id", table_name="agent_replay_bundles")
    op.drop_index("ix_agent_replay_bundles_thread_id", table_name="agent_replay_bundles")
    op.drop_table("agent_replay_bundles")

    op.execute(sa.text("DELETE FROM agent_trace_events WHERE source = 'REPLAY'"))
    op.execute(
        sa.text("ALTER TABLE agent_trace_events DROP CONSTRAINT ck_agent_trace_source_values")
    )
    op.execute(
        sa.text(
            "ALTER TABLE agent_trace_events ADD CONSTRAINT ck_agent_trace_source_values "
            "CHECK (source IN ('API','AGENT','MCP','DOMAIN','OUTBOX','RECONCILIATION'))"
        )
    )

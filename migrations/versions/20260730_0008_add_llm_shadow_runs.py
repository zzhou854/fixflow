"""add sanitized online provider shadow runs

Revision ID: 20260730_0008
Revises: 20260730_0007
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0008"
down_revision: str | None = "20260730_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_shadow_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_run_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("schema_version", sa.String(length=80), nullable=False),
        sa.Column("result_status", sa.String(length=16), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("structured_result_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "result_status IN ('SUCCEEDED','FAILED')",
            name=op.f("ck_llm_shadow_runs_llm_shadow_result_status_values"),
        ),
        sa.CheckConstraint(
            "latency_ms >= 0",
            name=op.f("ck_llm_shadow_runs_latency_nonnegative"),
        ),
        sa.CheckConstraint(
            "(result_status = 'SUCCEEDED' AND structured_result_hash IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(result_status = 'FAILED' AND structured_result_hash IS NULL "
            "AND error_code IS NOT NULL)",
            name=op.f("ck_llm_shadow_runs_result_payload_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id"],
            ["agent_runs.id"],
            name=op.f("fk_llm_shadow_runs_source_run_id_agent_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_shadow_runs")),
    )
    op.create_index(
        "ix_llm_shadow_runs_source_created",
        "llm_shadow_runs",
        ["source_run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_llm_shadow_runs_source_created", table_name="llm_shadow_runs")
    op.drop_table("llm_shadow_runs")

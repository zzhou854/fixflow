"""Add trace and aggregate links required by deterministic mutations.

Revision ID: 20260719_0002
Revises: 20260719_0001
Create Date: 2026-07-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_0002"
down_revision: str | Sequence[str] | None = "20260719_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LEGACY_TRACE_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    """Make every appointment transition and worker event traceable."""

    op.add_column(
        "appointment_status_history",
        sa.Column(
            "trace_id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.text(f"'{_LEGACY_TRACE_ID}'"),
        ),
    )
    op.alter_column("appointment_status_history", "trace_id", server_default=None)
    op.create_index(
        "ix_appointment_status_history_trace_id",
        "appointment_status_history",
        ["trace_id"],
    )

    op.add_column(
        "worker_events",
        sa.Column(
            "trace_id",
            sa.Uuid(),
            nullable=False,
            server_default=sa.text(f"'{_LEGACY_TRACE_ID}'"),
        ),
    )
    op.alter_column("worker_events", "trace_id", server_default=None)
    op.create_index("ix_worker_events_trace_id", "worker_events", ["trace_id"])


def downgrade() -> None:
    """Remove only the task-4 audit additions."""

    op.drop_index("ix_worker_events_trace_id", table_name="worker_events")
    op.drop_column("worker_events", "trace_id")
    op.drop_index(
        "ix_appointment_status_history_trace_id",
        table_name="appointment_status_history",
    )
    op.drop_column("appointment_status_history", "trace_id")

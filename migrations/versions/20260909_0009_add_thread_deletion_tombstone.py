"""add irreversible resident thread deletion tombstone

Revision ID: 20260909_0009
Revises: 20260730_0008
Create Date: 2026-09-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260909_0009"
down_revision: str | None = "20260730_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_thread_records",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "agent_thread_records",
        sa.Column("deleted_by_actor_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        op.f("fk_agent_thread_records_deleted_by_actor_id_users"),
        "agent_thread_records",
        "users",
        ["deleted_by_actor_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        op.f("ck_agent_thread_records_agent_thread_lifecycle_status_values"),
        "agent_thread_records",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_thread_records_archive_time_matches_status"),
        "agent_thread_records",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_thread_records_agent_thread_lifecycle_status_values"),
        "agent_thread_records",
        "lifecycle_status IN ('ACTIVE','ARCHIVED','DELETED')",
    )
    op.create_check_constraint(
        op.f("ck_agent_thread_records_archive_time_matches_status"),
        "agent_thread_records",
        "(lifecycle_status = 'ACTIVE' AND archived_at IS NULL AND deleted_at IS NULL) OR "
        "(lifecycle_status = 'ARCHIVED' AND archived_at IS NOT NULL AND deleted_at IS NULL) OR "
        "(lifecycle_status = 'DELETED' AND archived_at IS NOT NULL AND deleted_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_agent_thread_records_archive_time_matches_status"),
        "agent_thread_records",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_agent_thread_records_agent_thread_lifecycle_status_values"),
        "agent_thread_records",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_agent_thread_records_agent_thread_lifecycle_status_values"),
        "agent_thread_records",
        "lifecycle_status IN ('ACTIVE','ARCHIVED')",
    )
    op.create_check_constraint(
        op.f("ck_agent_thread_records_archive_time_matches_status"),
        "agent_thread_records",
        "(lifecycle_status = 'ACTIVE' AND archived_at IS NULL) OR "
        "(lifecycle_status = 'ARCHIVED' AND archived_at IS NOT NULL)",
    )
    op.drop_constraint(
        op.f("fk_agent_thread_records_deleted_by_actor_id_users"),
        "agent_thread_records",
        type_="foreignkey",
    )
    op.drop_column("agent_thread_records", "deleted_by_actor_id")
    op.drop_column("agent_thread_records", "deleted_at")

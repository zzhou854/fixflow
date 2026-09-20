"""Add versioned synthetic policy documents and pgvector chunks.

Revision ID: 20260720_0003
Revises: 20260719_0002
Create Date: 2026-07-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "20260720_0003"
down_revision: str | Sequence[str] | None = "20260719_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ISSUE_CATEGORIES = ("WATER_LEAK", "ELECTRICAL", "DOOR_LOCK")
_POLICY_TOPICS = (
    "SAFETY_ESCALATION",
    "RESPONSIBILITY_SCOPE",
    "IDENTITY_REQUIREMENT",
    "APPOINTMENT",
    "RESCHEDULING",
    "COMPLETION_ACCEPTANCE",
    "REWORK",
    "CANCELLATION",
    "HUMAN_ESCALATION",
)
_POLICY_EMBEDDING_DIMENSION = 384


def upgrade() -> None:
    """Create the minimal auditable policy retrieval schema."""

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "policy_documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("policy_code", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("issue_category", sa.String(32), nullable=True),
        sa.Column("policy_topic", sa.String(40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("authority_rank", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("embedding_provider", sa.String(255), nullable=False),
        sa.Column("embedding_model", sa.String(255), nullable=False),
        sa.Column("embedding_dimension", sa.Integer(), nullable=False),
        sa.Column("embedding_profile_version", sa.String(255), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_policy_documents"),
        sa.UniqueConstraint("policy_code", "version", name="uq_policy_documents_code_version"),
        sa.CheckConstraint("version > 0", name="ck_policy_documents_version_positive"),
        sa.CheckConstraint(
            "authority_rank BETWEEN 1 AND 100",
            name="ck_policy_documents_authority_rank_range",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_policy_documents_effective_period_valid",
        ),
        sa.CheckConstraint(
            "length(btrim(policy_code)) > 0 AND length(btrim(title)) > 0",
            name="ck_policy_documents_identity_present",
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_policy_documents_content_hash_sha256",
        ),
        sa.CheckConstraint(
            "length(btrim(embedding_provider)) > 0 AND "
            "length(btrim(embedding_model)) > 0 AND "
            "length(btrim(embedding_profile_version)) > 0",
            name="ck_policy_documents_embedding_profile_present",
        ),
        sa.CheckConstraint(
            f"embedding_dimension = {_POLICY_EMBEDDING_DIMENSION}",
            name="ck_policy_documents_embedding_dimension_fixed",
        ),
        sa.CheckConstraint(
            "issue_category IS NULL OR issue_category IN "
            + str(_ISSUE_CATEGORIES).replace('"', "'"),
            name="policy_issue_category_values",
        ),
        sa.CheckConstraint(
            "policy_topic IN " + str(_POLICY_TOPICS).replace('"', "'"),
            name="policy_topic_values",
        ),
        postgresql.ExcludeConstraint(
            ("policy_code", "="),
            (
                sa.func.tstzrange(
                    sa.column("effective_from"),
                    sa.func.coalesce(sa.column("effective_to"), sa.text("'infinity'::timestamptz")),
                    "[)",
                ),
                "&&",
            ),
            name="ex_policy_documents_code_effective_overlap",
            using="gist",
        ),
    )
    op.create_index("ix_policy_documents_issue_category", "policy_documents", ["issue_category"])
    op.create_index("ix_policy_documents_policy_topic", "policy_documents", ["policy_topic"])
    op.create_index("ix_policy_documents_effective_from", "policy_documents", ["effective_from"])

    op.create_table(
        "policy_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(_POLICY_EMBEDDING_DIMENSION), nullable=False),
        sa.Column("search_terms", postgresql.ARRAY(sa.String(255)), nullable=False),
        sa.Column("decision_key", sa.String(255), nullable=True),
        sa.Column("decision_value", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_policy_chunks"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["policy_documents.id"],
            name="fk_policy_chunks_document_id_policy_documents",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("document_id", "chunk_index", name="uq_policy_chunks_document_chunk"),
        sa.CheckConstraint("chunk_index >= 0", name="ck_policy_chunks_chunk_index_nonnegative"),
        sa.CheckConstraint("length(btrim(content)) > 0", name="ck_policy_chunks_content_present"),
        sa.CheckConstraint(
            "cardinality(search_terms) > 0", name="ck_policy_chunks_search_terms_present"
        ),
        sa.CheckConstraint(
            "(decision_key IS NULL AND decision_value IS NULL) OR "
            "(decision_key IS NOT NULL AND decision_value IS NOT NULL)",
            name="ck_policy_chunks_decision_pair_consistent",
        ),
    )
    op.create_index("ix_policy_chunks_document_id", "policy_chunks", ["document_id"])


def downgrade() -> None:
    """Remove policy tables but preserve shared extensions."""

    op.drop_index("ix_policy_chunks_document_id", table_name="policy_chunks")
    op.drop_table("policy_chunks")
    op.drop_index("ix_policy_documents_effective_from", table_name="policy_documents")
    op.drop_index("ix_policy_documents_policy_topic", table_name="policy_documents")
    op.drop_index("ix_policy_documents_issue_category", table_name="policy_documents")
    op.drop_table("policy_documents")

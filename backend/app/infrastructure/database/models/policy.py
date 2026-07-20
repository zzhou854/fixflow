"""Auditable synthetic policy documents and pgvector chunks."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import IssueCategory
from app.infrastructure.database.base import Base
from app.infrastructure.database.models.common import (
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)
from app.policy.constants import POLICY_EMBEDDING_DIMENSION
from app.policy.enums import PolicyTopic


class PolicyDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Versioned policy metadata; content is synthetic FixFlow demonstration data."""

    __tablename__ = "policy_documents"

    policy_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    issue_category: Mapped[IssueCategory | None] = mapped_column(
        string_enum(IssueCategory, name="policy_issue_category_values"), nullable=True
    )
    policy_topic: Mapped[PolicyTopic] = mapped_column(
        string_enum(PolicyTopic, name="policy_topic_values"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    authority_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_provider: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(255), nullable=False)
    embedding_dimension: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_profile_version: Mapped[str] = mapped_column(String(255), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    chunks: Mapped[list[PolicyChunk]] = relationship(back_populates="document")

    __table_args__ = (
        UniqueConstraint("policy_code", "version", name="uq_policy_documents_code_version"),
        Index("ix_policy_documents_issue_category", "issue_category"),
        Index("ix_policy_documents_policy_topic", "policy_topic"),
        Index("ix_policy_documents_effective_from", "effective_from"),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("authority_rank BETWEEN 1 AND 100", name="authority_rank_range"),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="effective_period_valid",
        ),
        CheckConstraint(
            "length(btrim(policy_code)) > 0 AND length(btrim(title)) > 0",
            name="identity_present",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash_sha256"),
        CheckConstraint(
            "length(btrim(embedding_provider)) > 0 AND "
            "length(btrim(embedding_model)) > 0 AND "
            "length(btrim(embedding_profile_version)) > 0",
            name="embedding_profile_present",
        ),
        CheckConstraint(
            f"embedding_dimension = {POLICY_EMBEDDING_DIMENSION}",
            name="embedding_dimension_fixed",
        ),
        ExcludeConstraint(
            ("policy_code", "="),
            (
                func.tstzrange(
                    effective_from,
                    func.coalesce(effective_to, text("'infinity'::timestamptz")),
                    "[)",
                ),
                "&&",
            ),
            name="ex_policy_documents_code_effective_overlap",
            using="gist",
        ),
    )


class PolicyChunk(UUIDPrimaryKeyMixin, Base):
    """Searchable policy evidence with a fixed-dimension vector."""

    __tablename__ = "policy_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_policy_chunks_document_chunk"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_nonnegative"),
        CheckConstraint("length(btrim(content)) > 0", name="content_present"),
        CheckConstraint("cardinality(search_terms) > 0", name="search_terms_present"),
        CheckConstraint(
            "(decision_key IS NULL AND decision_value IS NULL) OR "
            "(decision_key IS NOT NULL AND decision_value IS NOT NULL)",
            name="decision_pair_consistent",
        ),
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("policy_documents.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(
        Vector(POLICY_EMBEDDING_DIMENSION), nullable=False
    )
    search_terms: Mapped[list[str]] = mapped_column(ARRAY(String(255)), nullable=False)
    decision_key: Mapped[str | None] = mapped_column(String(255))
    decision_value: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped[PolicyDocument] = relationship(back_populates="chunks")

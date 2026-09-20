"""SQLAlchemy persistence and SQL-first filtering for policy retrieval."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domain.enums import IssueCategory
from app.infrastructure.database.models.policy import PolicyChunk, PolicyDocument
from app.policy.enums import PolicyTopic
from app.policy.models import (
    EmbeddingProfile,
    PolicyCandidate,
    PolicyDocumentInput,
    StoredPolicyIdentity,
)


class SqlAlchemyPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_identity(self, policy_code: str, version: int) -> StoredPolicyIdentity | None:
        row = (
            await self._session.execute(
                select(
                    PolicyDocument.id,
                    PolicyDocument.content_hash,
                    PolicyDocument.embedding_provider,
                    PolicyDocument.embedding_model,
                    PolicyDocument.embedding_dimension,
                    PolicyDocument.embedding_profile_version,
                ).where(
                    PolicyDocument.policy_code == policy_code,
                    PolicyDocument.version == version,
                )
            )
        ).one_or_none()
        if row is None:
            return None
        return StoredPolicyIdentity(
            document_id=row.id,
            content_hash=row.content_hash,
            embedding_profile=EmbeddingProfile(
                provider=row.embedding_provider,
                model=row.embedding_model,
                dimension=row.embedding_dimension,
                profile_version=row.embedding_profile_version,
            ),
        )

    async def add_document(
        self,
        document_id: UUID,
        document: PolicyDocumentInput,
        content_hash: str,
        embedding_profile: EmbeddingProfile,
    ) -> UUID:
        self._session.add(
            PolicyDocument(
                id=document_id,
                policy_code=document.policy_code,
                title=document.title,
                issue_category=document.issue_category,
                policy_topic=document.policy_topic,
                version=document.version,
                authority_rank=document.authority_rank,
                effective_from=document.effective_from,
                effective_to=document.effective_to,
                source_name=document.source_name,
                source_reference=document.source_reference,
                content_hash=content_hash,
                embedding_provider=embedding_profile.provider,
                embedding_model=embedding_profile.model,
                embedding_dimension=embedding_profile.dimension,
                embedding_profile_version=embedding_profile.profile_version,
                is_enabled=document.is_enabled,
            )
        )
        return document_id

    async def add_chunks(
        self,
        document_id: UUID,
        chunks: Sequence[
            tuple[UUID, int, str, Sequence[str], Sequence[float], str | None, str | None]
        ],
    ) -> None:
        rows: list[PolicyChunk] = []
        for chunk in chunks:
            chunk_id, index, content, search_terms, embedding, decision_key, decision_value = chunk
            rows.append(
                PolicyChunk(
                    id=chunk_id,
                    document_id=document_id,
                    chunk_index=index,
                    content=content,
                    search_terms=list(search_terms),
                    embedding=list(embedding),
                    decision_key=decision_key,
                    decision_value=decision_value,
                )
            )
        self._session.add_all(rows)

    async def list_effective_profiles(
        self,
        *,
        issue_category: str,
        policy_topics: Sequence[str],
        as_of: datetime,
    ) -> Sequence[EmbeddingProfile]:
        rows = (
            await self._session.execute(
                select(
                    PolicyDocument.embedding_provider,
                    PolicyDocument.embedding_model,
                    PolicyDocument.embedding_dimension,
                    PolicyDocument.embedding_profile_version,
                )
                .where(*self._effective_filters(issue_category, policy_topics, as_of))
                .distinct()
            )
        ).all()
        return [
            EmbeddingProfile(
                provider=row.embedding_provider,
                model=row.embedding_model,
                dimension=row.embedding_dimension,
                profile_version=row.embedding_profile_version,
            )
            for row in rows
        ]

    @staticmethod
    def _effective_filters(
        issue_category: str, policy_topics: Sequence[str], as_of: datetime
    ) -> tuple[ColumnElement[bool], ...]:
        return (
            PolicyDocument.is_enabled.is_(True),
            or_(
                PolicyDocument.issue_category == IssueCategory(issue_category),
                PolicyDocument.issue_category.is_(None),
            ),
            PolicyDocument.policy_topic.in_([PolicyTopic(value) for value in policy_topics]),
            PolicyDocument.effective_from <= as_of,
            or_(PolicyDocument.effective_to.is_(None), as_of < PolicyDocument.effective_to),
        )

    async def list_effective_candidates(
        self,
        *,
        issue_category: str,
        policy_topics: Sequence[str],
        as_of: datetime,
        query_embedding: Sequence[float],
        embedding_profile: EmbeddingProfile,
    ) -> Sequence[PolicyCandidate]:
        distance = PolicyChunk.embedding.cosine_distance(list(query_embedding)).label(
            "cosine_distance"
        )
        rows = (
            await self._session.execute(
                select(PolicyChunk, PolicyDocument, distance)
                .join(PolicyDocument, PolicyDocument.id == PolicyChunk.document_id)
                .where(
                    *self._effective_filters(issue_category, policy_topics, as_of),
                    PolicyDocument.embedding_provider == embedding_profile.provider,
                    PolicyDocument.embedding_model == embedding_profile.model,
                    PolicyDocument.embedding_dimension == embedding_profile.dimension,
                    PolicyDocument.embedding_profile_version == embedding_profile.profile_version,
                )
                .order_by(distance, PolicyChunk.id)
            )
        ).all()
        return [
            PolicyCandidate(
                chunk_id=chunk.id,
                document_id=document.id,
                policy_code=document.policy_code,
                policy_version=document.version,
                title=document.title,
                issue_category=document.issue_category,
                policy_topic=document.policy_topic,
                content=chunk.content,
                search_terms=tuple(chunk.search_terms),
                source_name=document.source_name,
                source_reference=document.source_reference,
                effective_from=document.effective_from,
                effective_to=document.effective_to,
                authority_rank=document.authority_rank,
                decision_key=chunk.decision_key,
                decision_value=chunk.decision_value,
                vector_similarity=max(-1.0, min(1.0, 1.0 - float(raw_distance))),
            )
            for chunk, document, raw_distance in rows
        ]

"""Focused ports for policy embeddings and PostgreSQL persistence."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from types import TracebackType
from typing import Protocol
from uuid import UUID

from app.policy.models import (
    EmbeddingProfile,
    EmbeddingResult,
    PolicyCandidate,
    PolicyDocumentInput,
    StoredPolicyIdentity,
)


class EmbeddingProvider(Protocol):
    @property
    def profile(self) -> EmbeddingProfile: ...
    async def embed_documents(self, texts: Sequence[str]) -> Sequence[EmbeddingResult]: ...
    async def embed_query(self, text: str) -> EmbeddingResult: ...
    async def health_check(self) -> bool: ...


class PolicyRepository(Protocol):
    async def get_identity(self, policy_code: str, version: int) -> StoredPolicyIdentity | None: ...
    async def add_document(
        self,
        document_id: UUID,
        document: PolicyDocumentInput,
        content_hash: str,
        embedding_profile: EmbeddingProfile,
    ) -> UUID: ...
    async def add_chunks(
        self,
        document_id: UUID,
        chunks: Sequence[
            tuple[UUID, int, str, Sequence[str], Sequence[float], str | None, str | None]
        ],
    ) -> None: ...
    async def list_effective_profiles(
        self,
        *,
        issue_category: str,
        policy_topics: Sequence[str],
        as_of: datetime,
    ) -> Sequence[EmbeddingProfile]: ...
    async def list_effective_candidates(
        self,
        *,
        issue_category: str,
        policy_topics: Sequence[str],
        as_of: datetime,
        query_embedding: Sequence[float],
        embedding_profile: EmbeddingProfile,
    ) -> Sequence[PolicyCandidate]: ...


class PolicyUnitOfWork(Protocol):
    policies: PolicyRepository

    async def __aenter__(self) -> PolicyUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    async def flush(self) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class PolicyUnitOfWorkFactory(Protocol):
    def __call__(self) -> PolicyUnitOfWork: ...

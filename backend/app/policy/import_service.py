"""Atomic, profile-safe, and reproducibly identified policy import."""

from uuid import UUID, uuid5

from app.policy.constants import POLICY_EMBEDDING_DIMENSION, POLICY_ID_NAMESPACE
from app.policy.errors import (
    EmbeddingDimensionMismatch,
    EmbeddingProfileConflict,
    EmbeddingProviderError,
    PolicyImportConflict,
)
from app.policy.models import (
    EmbeddingProfile,
    PolicyDocumentInput,
    PolicyImportResult,
    StoredPolicyIdentity,
)
from app.policy.normalization import stable_json_hash
from app.policy.ports import EmbeddingProvider, PolicyUnitOfWorkFactory


def policy_content_hash(document: PolicyDocumentInput) -> str:
    return stable_json_hash(document.model_dump(mode="json"))


def policy_document_id(document: PolicyDocumentInput, content_hash: str) -> UUID:
    identity = f"document:{document.policy_code}:{document.version}:{content_hash}"
    return uuid5(UUID(POLICY_ID_NAMESPACE), identity)


def policy_chunk_id(document: PolicyDocumentInput, content_hash: str, chunk_index: int) -> UUID:
    identity = f"chunk:{document.policy_code}:{document.version}:{chunk_index}:{content_hash}"
    return uuid5(UUID(POLICY_ID_NAMESPACE), identity)


def _validate_replay(
    existing: StoredPolicyIdentity, content_hash: str, profile: EmbeddingProfile
) -> None:
    if existing.content_hash != content_hash:
        raise PolicyImportConflict("the same policy code and version already has different content")
    if existing.embedding_profile != profile:
        raise EmbeddingProfileConflict(
            "the same policy content was imported with a different embedding profile"
        )


class PolicyImportService:
    def __init__(
        self,
        *,
        uow_factory: PolicyUnitOfWorkFactory,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._uow_factory = uow_factory
        self._embedding_provider = embedding_provider

    async def import_document(self, document: PolicyDocumentInput) -> PolicyImportResult:
        content_hash = policy_content_hash(document)
        profile = self._embedding_provider.profile
        if profile.dimension != POLICY_EMBEDDING_DIMENSION:
            raise EmbeddingDimensionMismatch("embedding provider profile dimension mismatch")
        async with self._uow_factory() as uow:
            existing = await uow.policies.get_identity(document.policy_code, document.version)
            if existing is not None:
                _validate_replay(existing, content_hash, profile)
                return PolicyImportResult(
                    document_id=existing.document_id,
                    content_hash=content_hash,
                    chunk_count=len(document.chunks),
                    replayed=True,
                )
        try:
            embeddings = tuple(
                await self._embedding_provider.embed_documents(
                    [chunk.content for chunk in document.chunks]
                )
            )
        except EmbeddingProviderError:
            raise
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise EmbeddingProviderError("embedding provider unavailable") from exc
        if len(embeddings) != len(document.chunks):
            raise EmbeddingProviderError("embedding provider returned an unexpected batch size")
        if any(len(item.vector) != POLICY_EMBEDDING_DIMENSION for item in embeddings):
            raise EmbeddingDimensionMismatch("embedding result dimension mismatch")
        if any(item.profile != profile for item in embeddings):
            raise EmbeddingProfileConflict("embedding batch returned inconsistent profiles")

        async with self._uow_factory() as uow:
            existing = await uow.policies.get_identity(document.policy_code, document.version)
            if existing is not None:
                _validate_replay(existing, content_hash, profile)
                return PolicyImportResult(
                    document_id=existing.document_id,
                    content_hash=content_hash,
                    chunk_count=len(document.chunks),
                    replayed=True,
                )
            document_id = policy_document_id(document, content_hash)
            await uow.policies.add_document(document_id, document, content_hash, profile)
            await uow.policies.add_chunks(
                document_id,
                [
                    (
                        policy_chunk_id(document, content_hash, index),
                        index,
                        chunk.content,
                        chunk.search_terms,
                        embedding.vector,
                        chunk.decision_key,
                        chunk.decision_value,
                    )
                    for index, (chunk, embedding) in enumerate(
                        zip(document.chunks, embeddings, strict=True)
                    )
                ],
            )
            await uow.flush()
            await uow.commit()
            return PolicyImportResult(
                document_id=document_id,
                content_hash=content_hash,
                chunk_count=len(document.chunks),
                replayed=False,
            )

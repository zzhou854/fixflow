"""Deterministic hybrid policy retrieval and evidence assembly."""

from datetime import UTC
from uuid import UUID

from app.policy.conflict import detect_policy_conflicts
from app.policy.constants import (
    MAX_POLICY_EVIDENCE_EXCERPT,
    POLICY_EMBEDDING_DIMENSION,
    RRF_K,
)
from app.policy.errors import (
    EmbeddingDimensionMismatch,
    EmbeddingProfileConflict,
    EmbeddingProviderError,
)
from app.policy.models import (
    EmbeddingProfile,
    PolicyCandidate,
    PolicyEvidence,
    PolicyRetrievalRequest,
    PolicyRetrievalResult,
)
from app.policy.normalization import normalize_policy_text, stable_json_hash
from app.policy.ports import EmbeddingProvider, PolicyUnitOfWorkFactory
from app.policy.sufficiency import evaluate_sufficiency

_INSTRUCTION_MARKERS = (
    "ignore system",
    "ignore previous",
    "忽略系统",
    "忽略以上",
    "直接关闭工单",
    "call tool",
)


def is_instruction_like(content: str) -> bool:
    normalized = normalize_policy_text(content)
    return any(marker in normalized for marker in _INSTRUCTION_MARKERS)


def lexical_match_score(query_text: str, search_terms: tuple[str, ...]) -> float:
    query = normalize_policy_text(query_text)
    normalized_terms = tuple(
        term for term in (normalize_policy_text(value) for value in search_terms) if term
    )
    if not normalized_terms:
        return 0.0
    return sum(term in query for term in normalized_terms) / len(normalized_terms)


def _lane_scores_and_ranks(
    candidates: tuple[PolicyCandidate, ...], request: PolicyRetrievalRequest
) -> tuple[dict[UUID, float], dict[UUID, float], dict[UUID, int], dict[UUID, int]]:
    lexical_scores = {
        item.chunk_id: lexical_match_score(request.query_text, item.search_terms)
        for item in candidates
    }
    vector_score_ranks = {
        score: rank
        for rank, score in enumerate(
            sorted(
                {
                    item.vector_similarity
                    for item in candidates
                    if item.vector_similarity >= request.minimum_vector_similarity
                },
                reverse=True,
            ),
            1,
        )
    }
    lexical_score_ranks = {
        score: rank
        for rank, score in enumerate(
            sorted({score for score in lexical_scores.values() if score > 0.0}, reverse=True),
            1,
        )
    }
    vector_scores = {
        item.chunk_id: item.vector_similarity
        for item in candidates
        if item.vector_similarity >= request.minimum_vector_similarity
    }
    return (
        lexical_scores,
        vector_scores,
        {
            item.chunk_id: vector_score_ranks[item.vector_similarity]
            for item in candidates
            if item.vector_similarity >= request.minimum_vector_similarity
        },
        {
            item.chunk_id: lexical_score_ranks[lexical_scores[item.chunk_id]]
            for item in candidates
            if lexical_scores[item.chunk_id] > 0.0
        },
    )


def _normalized_rrf(vector_rank: int | None, lexical_rank: int | None) -> float:
    raw = sum(1 / (RRF_K + rank) for rank in (vector_rank, lexical_rank) if rank is not None)
    return raw / (2 / (RRF_K + 1))


def fuse_candidates(
    candidates: tuple[PolicyCandidate, ...], request: PolicyRetrievalRequest
) -> tuple[PolicyEvidence, ...]:
    lexical_scores, vector_scores, vector_ranks, lexical_ranks = _lane_scores_and_ranks(
        candidates, request
    )
    evidence: list[PolicyEvidence] = []
    for item in candidates:
        vector_rank = vector_ranks.get(item.chunk_id)
        lexical_rank = lexical_ranks.get(item.chunk_id)
        if vector_rank is None and lexical_rank is None:
            continue
        evidence.append(
            PolicyEvidence(
                evidence_id=item.chunk_id,
                document_id=item.document_id,
                policy_code=item.policy_code,
                policy_version=item.policy_version,
                title=item.title,
                issue_category=item.issue_category,
                policy_topic=item.policy_topic,
                content_excerpt=item.content[:MAX_POLICY_EVIDENCE_EXCERPT],
                source_name=item.source_name,
                source_reference=item.source_reference,
                effective_from=item.effective_from,
                effective_to=item.effective_to,
                authority_rank=item.authority_rank,
                vector_similarity=vector_scores.get(item.chunk_id),
                vector_rank=vector_rank,
                lexical_score=lexical_scores.get(item.chunk_id),
                lexical_rank=lexical_rank,
                fusion_score=_normalized_rrf(vector_rank, lexical_rank),
                decision_key=item.decision_key,
                decision_value=item.decision_value,
                retrieved_as_of=request.as_of,
                instruction_like_content_detected=is_instruction_like(item.content),
            )
        )
    evidence.sort(
        key=lambda item: (
            -item.fusion_score,
            -item.authority_rank,
            -item.effective_from.timestamp(),
            str(item.evidence_id),
        )
    )
    return tuple(evidence[: request.top_k])


def policy_query_fingerprint(
    request: PolicyRetrievalRequest, embedding_profile: EmbeddingProfile
) -> str:
    return stable_json_hash(
        {
            "query_text": normalize_policy_text(request.query_text),
            "issue_category": request.issue_category.value,
            "policy_topics": sorted(topic.value for topic in request.policy_topics),
            "as_of": request.as_of.astimezone(UTC).isoformat(),
            "top_k": request.top_k,
            "minimum_vector_similarity": request.minimum_vector_similarity,
            "minimum_fusion_score": request.minimum_fusion_score,
            "embedding_profile": embedding_profile.model_dump(mode="json"),
            "intent_version": request.intent_version,
        }
    )


class PolicyRetrievalService:
    def __init__(
        self,
        *,
        uow_factory: PolicyUnitOfWorkFactory,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._uow_factory = uow_factory
        self._embedding_provider = embedding_provider

    async def retrieve(self, request: PolicyRetrievalRequest) -> PolicyRetrievalResult:
        profile = self._embedding_provider.profile
        if profile.dimension != POLICY_EMBEDDING_DIMENSION:
            raise EmbeddingDimensionMismatch("embedding provider profile dimension mismatch")
        try:
            query_embedding = await self._embedding_provider.embed_query(request.query_text)
        except EmbeddingProviderError:
            raise
        except (TimeoutError, ConnectionError, OSError) as exc:
            raise EmbeddingProviderError("embedding provider unavailable") from exc
        if len(query_embedding.vector) != POLICY_EMBEDDING_DIMENSION:
            raise EmbeddingDimensionMismatch("query embedding dimension mismatch")
        if query_embedding.profile != profile:
            raise EmbeddingProfileConflict("query embedding returned a different profile")
        async with self._uow_factory() as uow:
            stored_profiles = tuple(
                await uow.policies.list_effective_profiles(
                    issue_category=request.issue_category.value,
                    policy_topics=[topic.value for topic in request.policy_topics],
                    as_of=request.as_of,
                )
            )
            if any(stored_profile != profile for stored_profile in stored_profiles):
                raise EmbeddingProfileConflict(
                    "effective policy corpus does not match the query embedding profile"
                )
            candidates = tuple(
                await uow.policies.list_effective_candidates(
                    issue_category=request.issue_category.value,
                    policy_topics=[topic.value for topic in request.policy_topics],
                    as_of=request.as_of,
                    query_embedding=query_embedding.vector,
                    embedding_profile=profile,
                )
            )
        evidence = fuse_candidates(candidates, request)
        conflicts = detect_policy_conflicts(evidence)
        sufficiency, missing = evaluate_sufficiency(
            evidence=evidence,
            conflicts=conflicts,
            required_topics=request.policy_topics,
            minimum_vector_similarity=request.minimum_vector_similarity,
            minimum_fusion_score=request.minimum_fusion_score,
        )
        return PolicyRetrievalResult(
            evidence=evidence,
            conflicts=conflicts,
            sufficiency=sufficiency,
            missing_policy_topics=missing,
            retrieved_as_of=request.as_of,
            intent_version=request.intent_version,
            issue_category=request.issue_category,
            requested_policy_topics=request.policy_topics,
            embedding_profile=profile,
            policy_query_fingerprint=policy_query_fingerprint(request, profile),
        )

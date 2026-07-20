"""Frozen 21-case retrieval evaluation against real PostgreSQL and pgvector."""

from pathlib import Path
from uuid import uuid4

import pytest
from app.policy.evaluation import calculate_policy_metrics, load_policy_evaluation_set
from app.policy.models import PolicyRetrievalRequest
from app.policy.retrieval import PolicyRetrievalService


@pytest.mark.asyncio
async def test_frozen_policy_retrieval_evaluation(
    policy_services: tuple[object, ...],
) -> None:
    retriever = policy_services[1]
    assert isinstance(retriever, PolicyRetrievalService)
    evaluation = load_policy_evaluation_set(
        Path("backend/tests/fixtures/policy_retrieval_cases.json")
    )
    results = []
    for case in evaluation.cases:
        results.append(
            await retriever.retrieve(
                PolicyRetrievalRequest(
                    query_text=case.query_text,
                    issue_category=case.issue_category,
                    policy_topics=case.policy_topics,
                    as_of=case.as_of,
                    intent_version=1,
                    top_k=8,
                    minimum_vector_similarity=0.20,
                    minimum_fusion_score=0.25,
                    trace_id=uuid4(),
                )
            )
        )
    metrics = calculate_policy_metrics(evaluation.cases, tuple(results))
    assert metrics.case_count == 21
    assert metrics.recall_at_k >= 0.95
    assert metrics.mean_reciprocal_rank >= 0.80
    assert metrics.forbidden_policy_retrieval_rate == 0.0
    assert metrics.expired_policy_retrieval_rate == 0.0
    assert metrics.conflict_detection_accuracy == 1.0
    assert metrics.sufficiency_accuracy >= 0.95

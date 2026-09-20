"""Policy contracts, effective intervals, and deterministic hashes."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from app.domain.enums import IssueCategory
from app.policy.corpus import load_policy_corpus
from app.policy.effective import is_policy_effective
from app.policy.import_service import policy_chunk_id, policy_content_hash, policy_document_id
from app.policy.models import PolicyChunkInput, PolicyDocumentInput, PolicyRetrievalRequest
from pydantic import ValidationError


def test_effective_interval_is_half_open(policy_document: PolicyDocumentInput) -> None:
    assert is_policy_effective(policy_document, policy_document.effective_from)
    assert policy_document.effective_to is not None
    assert not is_policy_effective(policy_document, policy_document.effective_to)


def test_naive_effective_time_and_query_as_of_are_rejected(
    policy_document: PolicyDocumentInput,
) -> None:
    with pytest.raises(ValidationError):
        PolicyDocumentInput.model_validate(
            {**policy_document.model_dump(), "effective_from": datetime(2030, 1, 1)}
        )
    request = {
        "query_text": "漏水怎么办",
        "issue_category": "WATER_LEAK",
        "policy_topics": ["SAFETY_ESCALATION"],
        "as_of": datetime(2032, 1, 1),
        "intent_version": 1,
        "trace_id": "00000000-0000-0000-0000-000000000001",
    }
    with pytest.raises(ValidationError):
        PolicyRetrievalRequest.model_validate(request)


def test_invalid_effective_range_and_partial_decision_are_rejected(
    policy_document: PolicyDocumentInput,
) -> None:
    with pytest.raises(ValidationError):
        PolicyDocumentInput.model_validate(
            {
                **policy_document.model_dump(),
                "effective_to": datetime(2029, 1, 1, tzinfo=UTC),
            }
        )
    chunk = policy_document.chunks[0].model_dump()
    chunk["decision_value"] = None
    with pytest.raises(ValidationError):
        PolicyDocumentInput.model_validate({**policy_document.model_dump(), "chunks": [chunk]})


def test_content_hash_is_stable_and_changes_with_content(
    policy_document: PolicyDocumentInput,
) -> None:
    first = policy_content_hash(policy_document)
    assert first == policy_content_hash(policy_document.model_copy(deep=True))
    changed_chunk = policy_document.chunks[0].model_copy(update={"content": "不同内容"})
    changed = policy_document.model_copy(update={"chunks": (changed_chunk,)})
    assert policy_content_hash(changed) != first
    assert policy_document_id(policy_document, first) != policy_document_id(
        changed, policy_content_hash(changed)
    )
    assert policy_chunk_id(policy_document, first, 0) != policy_chunk_id(
        changed, policy_content_hash(changed), 0
    )


@pytest.mark.parametrize("content", ["", "   "])
def test_blank_policy_chunk_content_is_rejected(content: str) -> None:
    with pytest.raises(ValidationError):
        PolicyChunkInput(content=content, search_terms=("term",))


@pytest.mark.parametrize("terms", [(), ("",), ("   ",)])
def test_empty_or_blank_search_terms_are_rejected(terms: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        PolicyChunkInput(content="content", search_terms=terms)


def test_search_terms_are_normalized_and_deduplicated() -> None:
    chunk = PolicyChunkInput(
        content="content", search_terms=("  Safety Review ", "safety   review", "漏水")
    )
    assert chunk.search_terms == ("safety review", "漏水")


def test_synthetic_corpus_is_strict_and_has_expected_coverage() -> None:
    corpus = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json"))
    assert corpus.declaration == "Policy corpus is synthetic demonstration data for FixFlow."
    assert len(corpus.documents) == 21
    assert {document.issue_category for document in corpus.documents} >= {
        None,
        IssueCategory.WATER_LEAK,
        IssueCategory.ELECTRICAL,
        IssueCategory.DOOR_LOCK,
    }
    demo_as_of = datetime(2026, 7, 1, tzinfo=UTC)
    effective_codes = {
        document.policy_code
        for document in corpus.documents
        if document.is_enabled and is_policy_effective(document, demo_as_of)
    }
    assert {
        "WATER_NORMAL",
        "ELECTRICAL_OUTAGE",
        "LOCK_REPLACEMENT",
        "GENERAL_APPOINTMENT",
    } <= effective_codes
    assert {
        "EXPIRED_WATER_OLD",
        "FUTURE_LOCK_RULE",
        "DISABLED_ELECTRICAL",
    }.isdisjoint(effective_codes)

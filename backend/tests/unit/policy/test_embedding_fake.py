"""Deterministic embedding provider contract evidence."""

import pytest
from app.policy.constants import POLICY_EMBEDDING_DIMENSION
from app.policy.errors import EmbeddingProviderError

from tests.fakes.embedding import DeterministicEmbeddingProvider


@pytest.mark.asyncio
async def test_same_text_has_same_stable_dimension_vector() -> None:
    provider = DeterministicEmbeddingProvider()
    first = await provider.embed_query("厨房大量漏水")
    second = await provider.embed_query("厨房大量漏水")
    assert first.vector == second.vector
    assert len(first.vector) == POLICY_EMBEDDING_DIMENSION
    assert provider.profile.model_dump() == {
        "provider": "fixflow-test",
        "model": "character-bigram-hash",
        "dimension": 384,
        "profile_version": "v1",
    }
    assert first.profile == provider.profile


@pytest.mark.asyncio
async def test_related_text_is_more_similar_without_business_keyword_rules() -> None:
    provider = DeterministicEmbeddingProvider()
    base = await provider.embed_query("厨房持续漏水")
    related = await provider.embed_query("厨房漏水处理")
    unrelated = await provider.embed_query("门锁身份验证")
    related_dot = sum(a * b for a, b in zip(base.vector, related.vector, strict=True))
    unrelated_dot = sum(a * b for a, b in zip(base.vector, unrelated.vector, strict=True))
    assert related_dot > unrelated_dot


@pytest.mark.asyncio
async def test_blank_batch_bounds_and_scripted_failure_are_rejected() -> None:
    provider = DeterministicEmbeddingProvider(failures=[TimeoutError("timeout")])
    with pytest.raises(TimeoutError):
        await provider.embed_query("test")
    with pytest.raises(EmbeddingProviderError):
        await DeterministicEmbeddingProvider().embed_query("   ")
    with pytest.raises(EmbeddingProviderError):
        await DeterministicEmbeddingProvider().embed_documents([])

"""Deterministic test embedding provider with injectable failures."""

import hashlib
import math
from collections.abc import Sequence

from app.policy.constants import (
    MAX_POLICY_BATCH_CHARACTERS,
    MAX_POLICY_CHUNKS_PER_DOCUMENT,
    POLICY_EMBEDDING_DIMENSION,
)
from app.policy.errors import EmbeddingProviderError
from app.policy.models import EmbeddingProfile, EmbeddingResult
from app.policy.normalization import normalize_policy_text


def _tokens(text: str) -> tuple[str, ...]:
    normalized = normalize_policy_text(text)
    compact = normalized.replace(" ", "")
    words = tuple(normalized.split())
    bigrams = tuple(compact[index : index + 2] for index in range(max(0, len(compact) - 1)))
    return (*words, *bigrams) or (normalized,)


def _vector(text: str, dimension: int) -> tuple[float, ...]:
    values = [0.0] * dimension
    for token in _tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimension
        values[index] += 1.0
    norm = math.sqrt(sum(value * value for value in values))
    return tuple(value / norm for value in values)


class DeterministicEmbeddingProvider:
    def __init__(
        self,
        *,
        dimension: int = POLICY_EMBEDDING_DIMENSION,
        provider: str = "fixflow-test",
        model: str = "character-bigram-hash",
        profile_version: str = "v1",
        failures: Sequence[BaseException] = (),
        healthy: bool = True,
    ) -> None:
        self._dimension = dimension
        self._profile = EmbeddingProfile(
            provider=provider,
            model=model,
            dimension=dimension,
            profile_version=profile_version,
        )
        self._failures = list(failures)
        self._healthy = healthy
        self.document_calls: list[tuple[str, ...]] = []
        self.query_calls: list[str] = []

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    def _raise_scripted(self) -> None:
        if not self._failures:
            return
        error = self._failures.pop(0)
        if isinstance(error, BaseException):
            raise error
        raise EmbeddingProviderError("invalid scripted embedding failure")

    @staticmethod
    def _validate_text(text: str) -> None:
        if not text.strip():
            raise EmbeddingProviderError("embedding text must not be blank")

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[EmbeddingResult]:
        self._raise_scripted()
        batch = tuple(texts)
        if not batch or len(batch) > MAX_POLICY_CHUNKS_PER_DOCUMENT:
            raise EmbeddingProviderError("embedding batch size is outside allowed bounds")
        if sum(len(text) for text in batch) > MAX_POLICY_BATCH_CHARACTERS:
            raise EmbeddingProviderError("embedding batch exceeds character limit")
        for text in batch:
            self._validate_text(text)
        self.document_calls.append(batch)
        return [
            EmbeddingResult(
                vector=_vector(text, self._dimension),
                profile=self.profile,
            )
            for text in batch
        ]

    async def embed_query(self, text: str) -> EmbeddingResult:
        self._raise_scripted()
        self._validate_text(text)
        self.query_calls.append(text)
        return EmbeddingResult(
            vector=_vector(text, self._dimension),
            profile=self.profile,
        )

    async def health_check(self) -> bool:
        return self._healthy

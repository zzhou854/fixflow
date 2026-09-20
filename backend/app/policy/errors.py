"""Transport-neutral policy errors."""


class PolicyError(Exception):
    """Base error for deterministic policy operations."""


class PolicyImportConflict(PolicyError):
    """The same policy version was supplied with different content."""


class EmbeddingProviderError(PolicyError):
    """Embedding generation failed or violated its typed contract."""


class EmbeddingDimensionMismatch(EmbeddingProviderError):
    """An embedding vector does not match the frozen dimension."""


class EmbeddingProfileConflict(PolicyError):
    """Stored and requested embedding spaces are not identical."""


class StalePolicyResult(PolicyError):
    """A retrieval result no longer matches the current intent or request."""


class PolicyEffectivePeriodConflict(PolicyError):
    """Policy versions for one code have overlapping effective periods."""

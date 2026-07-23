"""Provider-neutral errors for structured interpretation."""

from __future__ import annotations

from enum import StrEnum


class LLMProviderErrorCode(StrEnum):
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    UPSTREAM_SERVER_ERROR = "UPSTREAM_SERVER_ERROR"
    INVALID_RESPONSE = "INVALID_RESPONSE"
    EMPTY_RESPONSE = "EMPTY_RESPONSE"
    INVALID_JSON = "INVALID_JSON"
    SCHEMA_VALIDATION_FAILED = "SCHEMA_VALIDATION_FAILED"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"
    CONTENT_FILTERED = "CONTENT_FILTERED"
    CONTEXT_LENGTH_EXCEEDED = "CONTEXT_LENGTH_EXCEEDED"
    REQUEST_REJECTED = "REQUEST_REJECTED"
    CANCELLED = "CANCELLED"
    UNKNOWN_PROVIDER_ERROR = "UNKNOWN_PROVIDER_ERROR"


class LLMProviderError(Exception):
    """A safe error boundary that never contains raw SDK data or user content."""

    def __init__(
        self,
        code: LLMProviderErrorCode,
        *,
        provider: str,
        model: str,
        retryable: bool,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
        attempt_count: int = 1,
        safe_detail: str = "structured interpretation failed",
        cause_type: str | None = None,
    ) -> None:
        super().__init__(safe_detail)
        self.code: LLMProviderErrorCode = code
        self.provider: str = provider
        self.model: str = model
        self.retryable: bool = retryable
        self.request_id: str | None = request_id
        self.retry_after_seconds: float | None = retry_after_seconds
        self.attempt_count: int = attempt_count
        self.safe_detail: str = safe_detail
        self.cause_type: str | None = cause_type

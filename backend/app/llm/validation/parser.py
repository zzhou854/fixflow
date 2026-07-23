"""Fail-closed JSON parser for structured interpretation."""

from __future__ import annotations

from pydantic import ValidationError

from app.agent.models import InterpretMessageOutput
from app.llm.errors import LLMProviderError, LLMProviderErrorCode
from app.llm.validation.invariants import InterpretationInvariantValidator


class StructuredInterpretationParser:
    def __init__(
        self,
        *,
        max_response_bytes: int,
        invariants: InterpretationInvariantValidator | None = None,
    ) -> None:
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self._max_response_bytes = max_response_bytes
        self._invariants = invariants or InterpretationInvariantValidator()

    def parse(self, content: object, *, provider: str, model: str) -> InterpretMessageOutput:
        if not isinstance(content, str) or not content.strip():
            raise self._error(LLMProviderErrorCode.EMPTY_RESPONSE, provider, model)
        if len(content.encode("utf-8")) > self._max_response_bytes:
            raise self._error(LLMProviderErrorCode.INVALID_RESPONSE, provider, model)
        try:
            value = InterpretMessageOutput.model_validate_json(content)
        except ValidationError as exc:
            code = (
                LLMProviderErrorCode.INVALID_JSON
                if any(item["type"] == "json_invalid" for item in exc.errors())
                else LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED
            )
            raise self._error(code, provider, model) from exc
        return self._invariants.validate(value, provider=provider, model=model)

    @staticmethod
    def _error(
        code: LLMProviderErrorCode,
        provider: str,
        model: str,
    ) -> LLMProviderError:
        return LLMProviderError(
            code,
            provider=provider,
            model=model,
            retryable=False,
            safe_detail="provider returned invalid structured output",
        )

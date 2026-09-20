"""Scripted implementation of the production LLMProvider protocol."""

from collections.abc import Sequence
from dataclasses import dataclass

from app.agent.models import (
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class StructuredCall:
    messages: tuple[LLMMessage, ...]
    response_model: type[BaseModel]
    config: LLMRequestConfig


@dataclass(frozen=True, slots=True)
class TextCall:
    messages: tuple[LLMMessage, ...]
    config: LLMRequestConfig


class ScriptedLLMProvider:
    """Returns only queued values; it contains no intent or business logic."""

    def __init__(
        self,
        *,
        structured: Sequence[dict[str, object] | Exception] = (),
        text: Sequence[str | Exception] = (),
        provider_name: str = "scripted",
        healthy: bool = True,
    ) -> None:
        self._structured = list(structured)
        self._text = list(text)
        self._provider_name = provider_name
        self._healthy = healthy
        self.structured_calls: list[StructuredCall] = []
        self.text_calls: list[TextCall] = []

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        self.structured_calls.append(StructuredCall(tuple(messages), response_model, model_config))
        if not self._structured:
            raise RuntimeError("no scripted structured result")
        value = self._structured.pop(0)
        if isinstance(value, Exception):
            raise value
        return StructuredLLMResult(
            payload=value,
            provider=self._provider_name,
            model=model_config.model,
            prompt_name=model_config.prompt_name,
            prompt_version=model_config.prompt_version,
        )

    async def generate_response(
        self,
        *,
        messages: Sequence[LLMMessage],
        model_config: LLMRequestConfig,
    ) -> TextLLMResult:
        self.text_calls.append(TextCall(tuple(messages), model_config))
        if not self._text:
            raise RuntimeError("no scripted text result")
        value = self._text.pop(0)
        if isinstance(value, Exception):
            raise value
        return TextLLMResult(
            text=value,
            provider=self._provider_name,
            model=model_config.model,
            prompt_name=model_config.prompt_name,
            prompt_version=model_config.prompt_version,
        )

    async def health_check(self) -> bool:
        return self._healthy

"""Provider-neutral LLM boundary for the two language-only nodes."""

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from app.agent.models import (
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)


class LLMProvider(Protocol):
    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult: ...

    async def generate_response(
        self,
        *,
        messages: Sequence[LLMMessage],
        model_config: LLMRequestConfig,
    ) -> TextLLMResult: ...

    async def health_check(self) -> bool: ...

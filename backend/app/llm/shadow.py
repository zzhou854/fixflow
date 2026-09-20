"""Best-effort shadow interpretation with a strict zero-influence boundary."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.agent.models import (
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from app.agent.ports import LLMProvider


class ShadowEvaluationRecord(BaseModel):
    """Safe operational metadata; provider payloads and source text are excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    recorded_at: datetime
    status: Literal["SUCCEEDED", "FAILED"]
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    prompt_name: str = Field(min_length=1, max_length=120)
    prompt_version: str = Field(min_length=1, max_length=40)
    schema_version: str | None = Field(default=None, max_length=80)
    latency_ms: int | None = Field(default=None, ge=0)
    attempt_count: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    error_type: str | None = Field(default=None, max_length=120)


class InMemoryShadowEvaluationSink:
    """Bounded, process-local demo sink; it is never a business fact source."""

    def __init__(self, *, max_records: int = 200) -> None:
        if max_records < 1:
            raise ValueError("max_records must be positive")
        self._records: deque[ShadowEvaluationRecord] = deque(maxlen=max_records)

    def append(self, record: ShadowEvaluationRecord) -> None:
        self._records.append(record)

    def snapshot(self) -> tuple[ShadowEvaluationRecord, ...]:
        return tuple(self._records)


class ShadowingLLMProvider:
    """Return only the scripted result and observe an isolated provider in background."""

    def __init__(
        self,
        *,
        primary: LLMProvider,
        shadow: LLMProvider,
        sink: InMemoryShadowEvaluationSink,
    ) -> None:
        self._primary = primary
        self._shadow = shadow
        self._sink = sink
        self._tasks: set[asyncio.Task[None]] = set()

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        primary_result = await self._primary.generate_structured(
            messages=messages,
            response_model=response_model,
            model_config=model_config,
        )
        task = asyncio.create_task(
            self._observe(
                messages=tuple(messages),
                response_model=response_model,
                model_config=model_config,
            ),
            name="fixflow-llm-shadow-observation",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return primary_result

    async def generate_response(
        self,
        *,
        messages: Sequence[LLMMessage],
        model_config: LLMRequestConfig,
    ) -> TextLLMResult:
        return await self._primary.generate_response(
            messages=messages,
            model_config=model_config,
        )

    async def health_check(self) -> bool:
        return await self._primary.health_check()

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        await self.drain()
        close = getattr(self._shadow, "close", None)
        if close is not None:
            await close()

    async def _observe(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> None:
        try:
            result = await self._shadow.generate_structured(
                messages=messages,
                response_model=response_model,
                model_config=model_config,
            )
        except Exception as exc:  # shadow failure is deliberately isolated
            self._sink.append(
                ShadowEvaluationRecord(
                    recorded_at=datetime.now(UTC),
                    status="FAILED",
                    provider="experimental-shadow",
                    model=model_config.model,
                    prompt_name=model_config.prompt_name,
                    prompt_version=model_config.prompt_version,
                    error_type=type(exc).__name__,
                )
            )
            return
        self._sink.append(
            ShadowEvaluationRecord(
                recorded_at=datetime.now(UTC),
                status="SUCCEEDED",
                provider=result.provider,
                model=result.model,
                prompt_name=result.prompt_name,
                prompt_version=result.prompt_version,
                schema_version=result.schema_version,
                latency_ms=result.latency_ms,
                attempt_count=result.attempt_count,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.total_tokens,
            )
        )

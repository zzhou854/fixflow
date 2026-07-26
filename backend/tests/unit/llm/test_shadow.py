from __future__ import annotations

import asyncio
from collections.abc import Sequence

from app.agent.models import (
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from app.llm.shadow import InMemoryShadowEvaluationSink, ShadowingLLMProvider
from pydantic import BaseModel


class Output(BaseModel):
    value: str


class Provider:
    def __init__(
        self,
        *,
        payload: str,
        release: asyncio.Event | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.release = release
        self.failure = failure
        self.structured_calls = 0
        self.response_calls = 0

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        self.structured_calls += 1
        if self.release is not None:
            await self.release.wait()
        if self.failure is not None:
            raise self.failure
        return StructuredLLMResult(
            payload={"value": self.payload},
            provider=f"provider-{self.payload}",
            model=model_config.model,
            prompt_name=model_config.prompt_name,
            prompt_version=model_config.prompt_version,
            schema_version="test-schema",
            latency_ms=3,
            attempt_count=1,
        )

    async def generate_response(
        self,
        *,
        messages: Sequence[LLMMessage],
        model_config: LLMRequestConfig,
    ) -> TextLLMResult:
        self.response_calls += 1
        return TextLLMResult(
            text=self.payload,
            provider=f"provider-{self.payload}",
            model=model_config.model,
            prompt_name=model_config.prompt_name,
            prompt_version=model_config.prompt_version,
        )

    async def health_check(self) -> bool:
        return True


def config() -> LLMRequestConfig:
    return LLMRequestConfig(
        model="scripted-model",
        prompt_name="resident_interpretation",
        prompt_version="1.0.0",
        temperature=0,
        max_output_tokens=100,
    )


async def test_shadow_result_cannot_delay_or_replace_primary_result() -> None:
    release = asyncio.Event()
    primary = Provider(payload="primary")
    shadow = Provider(payload="shadow", release=release)
    sink = InMemoryShadowEvaluationSink(max_records=2)
    provider = ShadowingLLMProvider(primary=primary, shadow=shadow, sink=sink)

    result = await provider.generate_structured(
        messages=(),
        response_model=Output,
        model_config=config(),
    )

    assert result.payload == {"value": "primary"}
    assert sink.snapshot() == ()
    release.set()
    await provider.drain()
    records = sink.snapshot()
    assert len(records) == 1
    assert records[0].status == "SUCCEEDED"
    assert records[0].provider == "provider-shadow"
    fields = type(records[0]).model_fields
    assert "payload" not in fields
    assert "messages" not in fields


async def test_shadow_failure_is_recorded_without_failing_business_result() -> None:
    primary = Provider(payload="primary")
    shadow = Provider(payload="shadow", failure=ConnectionError("synthetic failure"))
    sink = InMemoryShadowEvaluationSink()
    provider = ShadowingLLMProvider(primary=primary, shadow=shadow, sink=sink)

    result = await provider.generate_structured(
        messages=(),
        response_model=Output,
        model_config=config(),
    )
    await provider.drain()

    assert result.payload == {"value": "primary"}
    assert sink.snapshot()[0].status == "FAILED"
    assert sink.snapshot()[0].error_type == "ConnectionError"
    assert primary.structured_calls == 1
    assert shadow.structured_calls == 1


async def test_shadow_never_observes_response_composition() -> None:
    primary = Provider(payload="primary")
    shadow = Provider(payload="shadow")
    provider = ShadowingLLMProvider(
        primary=primary,
        shadow=shadow,
        sink=InMemoryShadowEvaluationSink(),
    )

    result = await provider.generate_response(messages=(), model_config=config())

    assert result.text == "primary"
    assert primary.response_calls == 1
    assert shadow.response_calls == 0

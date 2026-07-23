import asyncio
import json
import random
import time
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from app.agent.enums import LLMRole
from app.agent.models import InterpretMessageOutput, LLMMessage, LLMRequestConfig
from app.agent_runtime.execution_context import bind_execution_context
from app.llm.errors import LLMProviderError, LLMProviderErrorCode
from app.llm.prompts.registry import PromptRegistry
from app.llm.providers.zai_glm import (
    ZaiGLMConfig,
    ZaiGLMStructuredInterpretationProvider,
)
from app.llm.validation.parser import StructuredInterpretationParser


def _response(content: str, *, request_id: str = "req-1") -> object:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason="stop",
            )
        ],
        request_id=request_id,
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14),
    )


def _config(**overrides: object) -> ZaiGLMConfig:
    values: dict[str, object] = {
        "request_timeout_seconds": 1,
        "total_timeout_seconds": 2,
        "retry_initial_delay_seconds": 0,
        "retry_max_delay_seconds": 0,
    }
    values.update(overrides)
    return ZaiGLMConfig(**values)  # type: ignore[arg-type]


def _provider(create: object, **config: object) -> ZaiGLMStructuredInterpretationProvider:
    return ZaiGLMStructuredInterpretationProvider(
        prompt=PromptRegistry().resident_interpretation(),
        parser=StructuredInterpretationParser(max_response_bytes=65_536),
        config=_config(**config),
        completion_create=create,  # type: ignore[arg-type]
        sleeper=lambda _: asyncio.sleep(0),
        random_source=random.Random(0),
    )


def _request_config() -> LLMRequestConfig:
    return LLMRequestConfig(
        model="glm-5.1",
        prompt_name="resident_interpretation",
        prompt_version="1.0.0",
    )


@pytest.mark.asyncio
async def test_adapter_uses_official_json_mode_without_tools_or_streaming() -> None:
    calls: list[dict[str, object]] = []

    def create(**kwargs: object) -> object:
        calls.append(kwargs)
        return _response(json.dumps({"utterance_intent": "NEW_REPAIR"}))

    result = await _provider(create).generate_structured(
        messages=(
            LLMMessage(role=LLMRole.SYSTEM, content="system"),
            LLMMessage(role=LLMRole.USER, content="input"),
        ),
        response_model=InterpretMessageOutput,
        model_config=_request_config(),
    )
    call = calls[0]
    assert call["model"] == "glm-5.1"
    assert call["response_format"] == {"type": "json_object"}
    assert call["thinking"] == {"type": "disabled"}
    assert call["stream"] is False
    assert "tools" not in call
    assert result.payload["utterance_intent"] == "NEW_REPAIR"
    assert result.request_id == "req-1"
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (10, 4, 14)


@pytest.mark.asyncio
async def test_retry_is_bounded_to_transient_failures() -> None:
    attempts = 0

    def create(**_: object) -> object:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            error = RuntimeError("upstream")
            error.status_code = 503  # type: ignore[attr-defined]
            raise error
        return _response('{"utterance_intent":"UNKNOWN"}')

    result = await _provider(create).generate_structured(
        messages=(LLMMessage(role=LLMRole.USER, content="hello"),),
        response_model=InterpretMessageOutput,
        model_config=_request_config(),
    )
    assert attempts == 3
    assert result.attempt_count == 3


@pytest.mark.asyncio
async def test_invalid_json_is_not_retried() -> None:
    attempts = 0

    def create(**_: object) -> object:
        nonlocal attempts
        attempts += 1
        return _response("not-json")

    with pytest.raises(LLMProviderError) as caught:
        await _provider(create).generate_structured(
            messages=(LLMMessage(role=LLMRole.USER, content="hello"),),
            response_model=InterpretMessageOutput,
            model_config=_request_config(),
        )
    assert caught.value.code is LLMProviderErrorCode.INVALID_JSON
    assert attempts == 1


@pytest.mark.asyncio
async def test_sync_sdk_call_does_not_block_event_loop() -> None:
    def create(**_: object) -> object:
        time.sleep(0.05)
        return _response('{"utterance_intent":"UNKNOWN"}')

    ticked = False

    async def ticker() -> None:
        nonlocal ticked
        await asyncio.sleep(0.01)
        ticked = True

    provider_call = _provider(create).generate_structured(
        messages=(LLMMessage(role=LLMRole.USER, content="hello"),),
        response_model=InterpretMessageOutput,
        model_config=_request_config(),
    )
    await asyncio.gather(provider_call, ticker())
    assert ticked is True


@pytest.mark.asyncio
async def test_total_timeout_fails_safe() -> None:
    def create(**_: object) -> object:
        time.sleep(0.1)
        return _response('{"utterance_intent":"UNKNOWN"}')

    with pytest.raises(LLMProviderError) as caught:
        await _provider(
            create,
            request_timeout_seconds=0.01,
            total_timeout_seconds=0.02,
            max_attempts=3,
        ).generate_structured(
            messages=(LLMMessage(role=LLMRole.USER, content="hello"),),
            response_model=InterpretMessageOutput,
            model_config=_request_config(),
        )
    assert caught.value.code is LLMProviderErrorCode.TIMEOUT
    assert caught.value.retryable is False


@pytest.mark.asyncio
async def test_trace_contains_only_safe_provider_metadata() -> None:
    class FakeTrace:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        @staticmethod
        def event_key(run_id: object, event_type: str, suffix: str = "") -> str:
            return f"{run_id}:{event_type}:{suffix}"

        async def append_event(self, **kwargs: object) -> None:
            self.events.append(kwargs)

    trace = FakeTrace()

    def create(**_: object) -> object:
        return _response('{"utterance_intent":"UNKNOWN"}')

    run_id, thread_id, trace_id = uuid4(), uuid4(), uuid4()
    with bind_execution_context(
        run_id,
        thread_id,
        trace_id,
        cast(Any, trace),
    ):
        await _provider(create).generate_structured(
            messages=(LLMMessage(role=LLMRole.USER, content="private resident text"),),
            response_model=InterpretMessageOutput,
            model_config=_request_config(),
        )
    assert [event["event_type"] for event in trace.events] == [
        "llm_interpretation_started",
        "llm_interpretation_succeeded",
    ]
    encoded = repr(trace.events)
    assert "private resident text" not in encoded
    assert "api_key" not in encoded.casefold()
    assert "authorization" not in encoded.casefold()

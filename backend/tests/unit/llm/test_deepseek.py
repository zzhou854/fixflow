import asyncio
import json
import random

import httpx
import pytest
from app.agent.enums import LLMRole
from app.agent.models import InterpretMessageOutput, LLMMessage, LLMRequestConfig
from app.llm.errors import LLMProviderError, LLMProviderErrorCode
from app.llm.prompts.registry import PromptRegistry
from app.llm.providers.deepseek import (
    DeepSeekConfig,
    DeepSeekStructuredInterpretationProvider,
)
from app.llm.validation.parser import StructuredInterpretationParser


def _config(**overrides: object) -> DeepSeekConfig:
    values: dict[str, object] = {
        "request_timeout_seconds": 1,
        "total_timeout_seconds": 2,
        "retry_initial_delay_seconds": 0,
        "retry_max_delay_seconds": 0,
    }
    values.update(overrides)
    return DeepSeekConfig(**values)  # type: ignore[arg-type]


def _request_config() -> LLMRequestConfig:
    return LLMRequestConfig(
        model="deepseek-v4-flash",
        prompt_name="resident_interpretation",
        prompt_version="1.0.0",
    )


def _provider(
    handler: httpx.AsyncBaseTransport,
    sleeper: object | None = None,
    **config: object,
) -> DeepSeekStructuredInterpretationProvider:
    client = httpx.AsyncClient(
        transport=handler,
        base_url="https://api.deepseek.com/",
        headers={"Authorization": "Bearer synthetic-secret"},
    )
    return DeepSeekStructuredInterpretationProvider(
        prompt=PromptRegistry().resident_interpretation(),
        parser=StructuredInterpretationParser(max_response_bytes=65_536),
        config=_config(**config),
        client=client,
        sleeper=sleeper or (lambda _: asyncio.sleep(0)),  # type: ignore[arg-type]
        random_source=random.Random(0),
    )


def _success(content: str = '{"utterance_intent":"NEW_REPAIR"}') -> dict[str, object]:
    return {
        "id": "request-1",
        "choices": [
            {
                "message": {"content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
        },
    }


@pytest.mark.asyncio
async def test_adapter_uses_json_mode_disabled_thinking_and_no_tools() -> None:
    captured: dict[str, object] = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer synthetic-secret"
        assert request.url == "https://api.deepseek.com/chat/completions"
        return httpx.Response(200, json=_success(), headers={"x-request-id": "header-id"})

    provider = _provider(httpx.MockTransport(handle))
    result = await provider.generate_structured(
        messages=(
            LLMMessage(role=LLMRole.SYSTEM, content="system"),
            LLMMessage(role=LLMRole.USER, content="input"),
        ),
        response_model=InterpretMessageOutput,
        model_config=_request_config(),
    )

    assert captured["model"] == "deepseek-v4-flash"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["thinking"] == {"type": "disabled"}
    assert captured["stream"] is False
    assert "tools" not in captured
    assert result.provider == "deepseek"
    assert result.payload["utterance_intent"] == "NEW_REPAIR"
    assert result.request_id == "header-id"
    assert (result.input_tokens, result.output_tokens, result.total_tokens) == (10, 4, 14)
    await provider.close()


@pytest.mark.asyncio
async def test_empty_json_mode_response_fails_closed_without_retry() -> None:
    attempts = 0

    async def handle(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(200, json=_success(""))

    provider = _provider(httpx.MockTransport(handle))
    with pytest.raises(LLMProviderError) as caught:
        await provider.generate_structured(
            messages=(LLMMessage(role=LLMRole.USER, content="input"),),
            response_model=InterpretMessageOutput,
            model_config=_request_config(),
        )
    assert caught.value.code is LLMProviderErrorCode.EMPTY_RESPONSE
    assert attempts == 1
    await provider.close()


@pytest.mark.asyncio
async def test_rate_limit_retry_respects_retry_after_and_is_bounded() -> None:
    attempts = 0
    delays: list[float] = []

    async def handle(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(
                429, json={"error": {"message": "limited"}}, headers={"retry-after": "2"}
            )
        return httpx.Response(200, json=_success())

    provider = _provider(
        httpx.MockTransport(handle),
        sleeper=lambda delay: _record_delay(delays, delay),
        max_attempts=3,
    )
    result = await provider.generate_structured(
        messages=(LLMMessage(role=LLMRole.USER, content="input"),),
        response_model=InterpretMessageOutput,
        model_config=_request_config(),
    )
    assert result.attempt_count == 3
    assert attempts == 3
    assert delays == [2, 2]
    await provider.close()


async def _record_delay(delays: list[float], delay: float) -> None:
    delays.append(delay)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, LLMProviderErrorCode.AUTHENTICATION_FAILED),
        (403, LLMProviderErrorCode.PERMISSION_DENIED),
        (400, LLMProviderErrorCode.REQUEST_REJECTED),
    ],
)
async def test_non_retryable_http_errors_are_safely_mapped(
    status: int,
    expected: LLMProviderErrorCode,
) -> None:
    async def handle(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"error": {"message": "secret material must not escape"}},
        )

    provider = _provider(httpx.MockTransport(handle))
    with pytest.raises(LLMProviderError) as caught:
        await provider.generate_structured(
            messages=(LLMMessage(role=LLMRole.USER, content="private text"),),
            response_model=InterpretMessageOutput,
            model_config=_request_config(),
        )
    assert caught.value.code is expected
    assert "secret material" not in str(caught.value)
    assert "private text" not in str(caught.value)
    await provider.close()


def test_provider_rejects_other_model_or_enabled_thinking() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=_success()))
    client = httpx.AsyncClient(transport=transport)
    prompt = PromptRegistry().resident_interpretation()
    parser = StructuredInterpretationParser(max_response_bytes=65_536)
    with pytest.raises(ValueError, match="deepseek-v4-flash"):
        DeepSeekStructuredInterpretationProvider(
            prompt=prompt,
            parser=parser,
            client=client,
            config=_config(model="deepseek-chat"),
        )
    with pytest.raises(ValueError, match="disabled thinking"):
        DeepSeekStructuredInterpretationProvider(
            prompt=prompt,
            parser=parser,
            client=client,
            config=_config(thinking_mode="enabled"),
        )

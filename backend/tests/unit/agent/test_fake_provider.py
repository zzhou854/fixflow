"""Scripted provider behavior without business-logic emulation."""

import pytest
from app.agent.enums import AgentIntent, LLMRole
from app.agent.models import InterpretMessageOutput, LLMMessage, LLMRequestConfig

from tests.fakes.llm import ScriptedLLMProvider


def _config() -> LLMRequestConfig:
    return LLMRequestConfig(
        model="test-model", prompt_name="interpret_message", prompt_version="v1"
    )


@pytest.mark.asyncio
async def test_records_and_returns_scripted_structured_result() -> None:
    provider = ScriptedLLMProvider(structured=[{"utterance_intent": AgentIntent.NEW_REPAIR}])
    result = await provider.generate_structured(
        messages=(LLMMessage(role=LLMRole.USER, content="hello"),),
        response_model=InterpretMessageOutput,
        model_config=_config(),
    )
    assert result.payload["utterance_intent"] is AgentIntent.NEW_REPAIR
    assert provider.structured_calls[0].response_model is InterpretMessageOutput


@pytest.mark.asyncio
async def test_records_and_returns_scripted_text() -> None:
    provider = ScriptedLLMProvider(text=["Please provide the room location."])
    result = await provider.generate_response(
        messages=(LLMMessage(role=LLMRole.USER, content="hello"),),
        model_config=_config(),
    )
    assert result.text.startswith("Please")
    assert len(provider.text_calls) == 1


@pytest.mark.asyncio
async def test_injects_timeout_without_translating_it() -> None:
    provider = ScriptedLLMProvider(structured=[TimeoutError("scripted timeout")])
    with pytest.raises(TimeoutError):
        await provider.generate_structured(
            messages=(LLMMessage(role=LLMRole.USER, content="hello"),),
            response_model=InterpretMessageOutput,
            model_config=_config(),
        )


@pytest.mark.asyncio
async def test_can_return_invalid_payload_for_node_validation() -> None:
    provider = ScriptedLLMProvider(structured=[{"unexpected": True}])
    result = await provider.generate_structured(
        messages=(LLMMessage(role=LLMRole.USER, content="hello"),),
        response_model=InterpretMessageOutput,
        model_config=_config(),
    )
    assert result.payload == {"unexpected": True}


@pytest.mark.asyncio
async def test_health_is_scripted_and_no_keyword_logic_exists() -> None:
    provider = ScriptedLLMProvider(healthy=False)
    assert await provider.health_check() is False

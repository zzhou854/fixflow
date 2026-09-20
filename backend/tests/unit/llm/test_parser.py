import json

import pytest
from app.agent.enums import AgentIntent
from app.llm.errors import LLMProviderError, LLMProviderErrorCode
from app.llm.validation.parser import StructuredInterpretationParser


def _parser() -> StructuredInterpretationParser:
    return StructuredInterpretationParser(max_response_bytes=1024)


def test_parser_returns_formal_schema() -> None:
    result = _parser().parse(
        json.dumps({"utterance_intent": "NEW_REPAIR", "issue_category": "WATER_LEAK"}),
        provider="zai",
        model="glm-5.1",
    )
    assert result.utterance_intent is AgentIntent.NEW_REPAIR


@pytest.mark.parametrize(
    ("content", "code"),
    [
        ("", LLMProviderErrorCode.EMPTY_RESPONSE),
        ("```json\n{}\n```", LLMProviderErrorCode.INVALID_JSON),
        (
            '{"utterance_intent":"NEW_REPAIR","extra":1}',
            LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED,
        ),
        (
            '{"utterance_intent":"REQUEST_HUMAN","requested_human":false}',
            LLMProviderErrorCode.INVARIANT_VIOLATION,
        ),
        (
            '{"utterance_intent":"ACCEPT_REPAIR","acceptance_decision":"REJECT"}',
            LLMProviderErrorCode.INVARIANT_VIOLATION,
        ),
    ],
)
def test_parser_fails_closed_without_json_repair(content: str, code: LLMProviderErrorCode) -> None:
    with pytest.raises(LLMProviderError) as caught:
        _parser().parse(content, provider="zai", model="glm-5.1")
    assert caught.value.code is code
    assert caught.value.retryable is False


def test_parser_rejects_oversized_response() -> None:
    with pytest.raises(LLMProviderError) as caught:
        StructuredInterpretationParser(max_response_bytes=8).parse(
            '{"utterance_intent":"UNKNOWN"}',
            provider="zai",
            model="glm-5.1",
        )
    assert caught.value.code is LLMProviderErrorCode.INVALID_RESPONSE

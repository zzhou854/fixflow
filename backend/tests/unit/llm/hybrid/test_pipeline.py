from __future__ import annotations

from collections.abc import Sequence

import pytest
from app.agent.errors import ProviderExhausted
from app.agent.models import (
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from app.llm.errors import LLMProviderErrorCode
from app.llm.hybrid.models import (
    ControlledVerificationResult,
    ExtractedResidentFactsV2,
    VerificationVerdict,
)
from app.llm.hybrid.pipeline import HybridInterpretationNode
from app.llm.online.routing import ProviderExhaustedError
from pydantic import BaseModel

from tests.unit.llm.hybrid.conftest import empty_facts
from tests.unit.llm.hybrid.test_rules import node_input


class FactProvider:
    def __init__(self, facts: ExtractedResidentFactsV2) -> None:
        self.facts = facts
        self.response_models: list[type[BaseModel]] = []

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        del messages
        self.response_models.append(response_model)
        return StructuredLLMResult(
            payload=self.facts.model_dump(mode="json"),
            provider="fake-facts",
            model=model_config.model,
            prompt_name=model_config.prompt_name,
            prompt_version=model_config.prompt_version,
            schema_version="resident-facts-v2",
        )

    async def generate_response(
        self,
        *,
        messages: Sequence[LLMMessage],
        model_config: LLMRequestConfig,
    ) -> TextLLMResult:
        del messages, model_config
        raise AssertionError("hybrid interpretation must not use free text")

    async def health_check(self) -> bool:
        return True


class ExhaustedProvider(FactProvider):
    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        del messages, response_model, model_config
        raise ProviderExhaustedError(
            LLMProviderErrorCode.TIMEOUT,
            provider="deepseek",
            model="deepseek-v4-pro",
            retryable=False,
        )


@pytest.mark.asyncio
async def test_pipeline_returns_existing_formal_schema_without_tools() -> None:
    provider = FactProvider(empty_facts())
    node = HybridInterpretationNode(provider, model="fact-model")

    result, diagnostics = await node.interpret_with_diagnostics(
        node_input("厨房水管漏水，请安排维修。")
    )

    assert provider.response_models == [ExtractedResidentFactsV2]
    assert result.interpretation.utterance_intent.value == "NEW_REPAIR"
    assert result.interpretation.issue_category is not None
    assert result.interpretation.issue_category.value == "WATER_LEAK"
    assert result.metadata.schema_version == "interpretation-result-v1"
    assert result.metadata.transport_tool_call_count == 0
    assert diagnostics.metadata.architecture_id == "hybrid_interpretation"
    assert diagnostics.metadata.verification_call_count == 0


@pytest.mark.asyncio
async def test_controlled_verification_is_bounded_and_cannot_replace_result() -> None:
    calls: list[tuple[str, ...]] = []

    async def verifier(
        _node_input: object,
        _facts: object,
        conflicts: tuple[str, ...],
    ) -> ControlledVerificationResult:
        calls.append(conflicts)
        return ControlledVerificationResult(verdict=VerificationVerdict.INSUFFICIENT_EVIDENCE)

    provider = FactProvider(empty_facts())
    node = HybridInterpretationNode(provider, model="fact-model", verifier=verifier)
    result, diagnostics = await node.interpret_with_diagnostics(
        node_input("我没有现有预约，但想说改期。")
    )

    assert len(calls) == 1
    assert result.interpretation.utterance_intent.value == "RESCHEDULE_APPOINTMENT"
    assert diagnostics.metadata.verification_call_count == 1
    assert diagnostics.metadata.verification_result is VerificationVerdict.INSUFFICIENT_EVIDENCE


@pytest.mark.asyncio
async def test_provider_exhaustion_crosses_agent_boundary_as_typed_failure() -> None:
    node = HybridInterpretationNode(ExhaustedProvider(empty_facts()), model="fact-model")
    with pytest.raises(ProviderExhausted):
        await node(node_input("厨房漏水"))

"""Response node evidence boundary and failure tests."""

from uuid import uuid4

import pytest
from app.agent.errors import LLMProviderUnavailable, LLMTimeout
from app.agent.models import (
    AllowedPolicyEvidence,
    ComposeResponseInput,
    SafeErrorInformation,
    VerifiedBusinessFact,
)
from app.agent.nodes.compose_response import ComposeResponseNode
from app.domain.enums import WorkflowStage

from tests.fakes.llm import ScriptedLLMProvider


def _input(**changes: object) -> ComposeResponseInput:
    payload: dict[str, object] = {
        "verified_business_facts": (
            VerifiedBusinessFact(
                fact_id=uuid4(), fact_type="TICKET_STATE", statement="工单仍为 OPEN"
            ),
        ),
        "allowed_policy_evidence": (),
        "current_workflow_stage": WorkflowStage.NEED_INFO,
        "required_user_action": "请补充具体故障位置",
    }
    payload.update(changes)
    return ComposeResponseInput.model_validate(payload)


@pytest.mark.asyncio
async def test_composes_from_verified_facts_and_records_prompt_version() -> None:
    provider = ScriptedLLMProvider(text=["工单尚未完成，请补充具体故障位置。"])
    result = await ComposeResponseNode(provider, model="test-model")(_input())
    assert "尚未完成" in result.response_text
    assert result.metadata.prompt_name == "compose_response"
    assert result.metadata.prompt_version == "v1"
    assert "工单仍为 OPEN" in provider.text_calls[0].messages[1].content


@pytest.mark.asyncio
async def test_prompt_and_schema_bound_composition_context_to_allowlisted_evidence() -> None:
    provider = ScriptedLLMProvider(text=["目前尚未创建预约，请先选择候选时间。"])
    await ComposeResponseNode(provider, model="test-model")(_input())
    system = provider.text_calls[0].messages[0].content
    assert "Never invent" in system
    assert "Candidate slots are not bookings" in system


@pytest.mark.asyncio
async def test_prompt_contains_only_supplied_fact_and_evidence_records() -> None:
    fact = VerifiedBusinessFact(
        fact_id=uuid4(), fact_type="TICKET_STATE", statement="工单状态为 OPEN"
    )
    evidence = AllowedPolicyEvidence(evidence_id=uuid4(), statement="紧急风险需要人工处理")
    provider = ScriptedLLMProvider(text=["请等待人工处理。"])
    await ComposeResponseNode(provider, model="test-model")(
        _input(verified_business_facts=(fact,), allowed_policy_evidence=(evidence,))
    )
    context = provider.text_calls[0].messages[1].content
    assert str(fact.fact_id) in context
    assert fact.statement in context
    assert str(evidence.evidence_id) in context
    assert evidence.statement in context
    assert "CLOSED" not in context


@pytest.mark.asyncio
async def test_no_policy_evidence_is_explicit_in_bounded_context() -> None:
    provider = ScriptedLLMProvider(text=["目前没有可引用的政策证据。"])
    await ComposeResponseNode(provider, model="test-model")(_input())
    user_context = provider.text_calls[0].messages[1].content
    assert '"allowed_policy_evidence":[]' in user_context


@pytest.mark.asyncio
async def test_insufficient_facts_require_explicit_next_action() -> None:
    provider = ScriptedLLMProvider(text=["当前尚未完成，请补充故障位置。"])
    result = await ComposeResponseNode(provider, model="test-model")(
        _input(
            verified_business_facts=(),
            required_user_action="请补充故障位置",
        )
    )
    assert "尚未完成" in result.response_text
    assert "请补充故障位置" in provider.text_calls[0].messages[1].content


@pytest.mark.asyncio
async def test_allowed_policy_evidence_is_the_only_policy_context() -> None:
    evidence = AllowedPolicyEvidence(evidence_id=uuid4(), statement="紧急风险需人工处理")
    provider = ScriptedLLMProvider(text=["根据已核验政策，该风险需要人工处理。"])
    await ComposeResponseNode(provider, model="test-model")(
        _input(allowed_policy_evidence=(evidence,))
    )
    assert "紧急风险需人工处理" in provider.text_calls[0].messages[1].content


@pytest.mark.asyncio
async def test_human_review_stage_is_not_hidden() -> None:
    provider = ScriptedLLMProvider(text=["当前已进入人工处理，请等待物业人员跟进。"])
    result = await ComposeResponseNode(provider, model="test-model")(
        _input(
            current_workflow_stage=WorkflowStage.HUMAN_REVIEW,
            required_user_action="请等待物业人员联系",
        )
    )
    assert "人工" in result.response_text
    assert "HUMAN_REVIEW" in provider.text_calls[0].messages[1].content


@pytest.mark.asyncio
async def test_only_safe_error_is_sent_not_internal_exception() -> None:
    safe = SafeErrorInformation(
        code="SERVICE_UNAVAILABLE", message="服务暂时不可用", retryable=True
    )
    provider = ScriptedLLMProvider(text=["服务暂时不可用，请稍后重试。"])
    await ComposeResponseNode(provider, model="test-model")(_input(safe_error_information=safe))
    context = provider.text_calls[0].messages[1].content
    assert "服务暂时不可用" in context
    assert "Traceback" not in context
    assert "SELECT " not in context


@pytest.mark.asyncio
async def test_compose_timeout_and_unavailable_preserve_exception_chain() -> None:
    timeout_provider = ScriptedLLMProvider(text=[TimeoutError("timeout")])
    with pytest.raises(LLMTimeout) as timeout:
        await ComposeResponseNode(timeout_provider, model="test-model")(_input())
    assert isinstance(timeout.value.__cause__, TimeoutError)

    offline_provider = ScriptedLLMProvider(text=[ConnectionError("offline")])
    with pytest.raises(LLMProviderUnavailable) as unavailable:
        await ComposeResponseNode(offline_provider, model="test-model")(_input())
    assert isinstance(unavailable.value.__cause__, ConnectionError)

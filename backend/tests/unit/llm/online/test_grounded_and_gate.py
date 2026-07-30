from __future__ import annotations

from collections.abc import Sequence

import pytest
from app.agent.models import (
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from app.llm.online.gate import (
    OnlineProviderGate,
    OnlineRuntimeMode,
    ProviderPurpose,
    QualificationStatus,
)
from app.llm.online.grounded import (
    GroundedFact,
    GroundedResponseProvider,
    GroundedResponseRequest,
)
from pydantic import BaseModel


class DraftProvider:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls = 0

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        del messages, response_model, model_config
        self.calls += 1
        return StructuredLLMResult(
            payload=self.payload,
            provider="deepseek",
            model="deepseek-v4-flash",
            prompt_name="grounded_response",
            prompt_version="1.0.0",
        )

    async def generate_response(
        self,
        *,
        messages: Sequence[LLMMessage],
        model_config: LLMRequestConfig,
    ) -> TextLLMResult:
        raise AssertionError

    async def health_check(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_critical_business_result_bypasses_model_and_preserves_outcome() -> None:
    model = DraftProvider({})
    service = GroundedResponseProvider(model, model="deepseek-v4-flash")
    result = await service.compose(
        GroundedResponseRequest(
            template_id="TICKET_CREATED",
            message_outcome="COMPLETED",
            required_user_action="选择上门时间",
        )
    )
    assert model.calls == 0
    assert result.message_outcome == "COMPLETED"
    assert result.required_user_action == "选择上门时间"
    assert result.used_model is False


@pytest.mark.asyncio
async def test_draft_can_only_select_server_owned_facts_and_tone() -> None:
    model = DraftProvider(
        {
            "template_id": "GENERIC_UPDATE",
            "tone": "WARM",
            "included_fact_ids": ["ticket.status"],
        }
    )
    service = GroundedResponseProvider(model, model="deepseek-v4-flash")
    result = await service.compose(
        GroundedResponseRequest(
            template_id="GENERIC_UPDATE",
            message_outcome="COMPLETED",
            facts=(GroundedFact(fact_id="ticket.status", safe_text="工单正在处理中。"),),
        )
    )
    assert "工单正在处理中" in result.text
    assert result.used_model is True


@pytest.mark.asyncio
async def test_unverified_fact_or_changed_template_falls_back_deterministically() -> None:
    model = DraftProvider(
        {
            "template_id": "OTHER_TEMPLATE",
            "tone": "WARM",
            "included_fact_ids": ["invented.fact"],
        }
    )
    service = GroundedResponseProvider(model, model="deepseek-v4-flash")
    result = await service.compose(
        GroundedResponseRequest(
            template_id="GENERIC_UPDATE",
            message_outcome="COMPLETED",
        )
    )
    assert result.used_model is False
    assert result.message_outcome == "COMPLETED"


@pytest.mark.asyncio
async def test_model_cannot_invent_ticket_time_or_policy_fields() -> None:
    model = DraftProvider(
        {
            "template_id": "GENERIC_UPDATE",
            "tone": "WARM",
            "included_fact_ids": [],
            "ticket_id": "invented",
            "appointment_time": "invented",
            "policy_text": "invented",
        }
    )
    result = await GroundedResponseProvider(model, model="deepseek-v4-flash").compose(
        GroundedResponseRequest(
            template_id="GENERIC_UPDATE",
            message_outcome="COMPLETED",
        )
    )
    assert result.used_model is False
    assert "invented" not in result.text


def test_default_gate_keeps_online_provider_inactive() -> None:
    gate = OnlineProviderGate()
    assert not any(gate.allows(purpose) for purpose in ProviderPurpose)


def test_development_gate_allows_only_explicit_test_calls() -> None:
    gate = OnlineProviderGate(enabled=True, mode=OnlineRuntimeMode.DEVELOPMENT)
    assert gate.allows(ProviderPurpose.TEST)
    assert not gate.allows(ProviderPurpose.BUSINESS)
    assert not gate.allows(ProviderPurpose.SHADOW)


def test_shadow_requires_explicit_switch_and_contract_evidence() -> None:
    insufficient = OnlineProviderGate(
        enabled=True,
        shadow_enabled=True,
        mode=OnlineRuntimeMode.DEMO_SAFE,
        qualification=QualificationStatus.NOT_ACTIVATED,
    )
    allowed = OnlineProviderGate(
        enabled=True,
        shadow_enabled=True,
        mode=OnlineRuntimeMode.DEMO_SAFE,
        qualification=QualificationStatus.CONTRACT_PASSED,
    )
    assert not insufficient.allows(ProviderPurpose.SHADOW)
    assert allowed.allows(ProviderPurpose.SHADOW)
    assert not allowed.allows(ProviderPurpose.BUSINESS)


def test_business_calls_require_future_explicit_approval() -> None:
    gate = OnlineProviderGate(
        enabled=True,
        mode=OnlineRuntimeMode.PRODUCTION_CANDIDATE,
        qualification=QualificationStatus.CANARY_PASSED,
    )
    assert not gate.allows(ProviderPurpose.BUSINESS)
    approved = OnlineProviderGate(
        enabled=True,
        mode=OnlineRuntimeMode.PRODUCTION_CANDIDATE,
        qualification=QualificationStatus.APPROVED,
    )
    assert approved.allows(ProviderPurpose.BUSINESS)

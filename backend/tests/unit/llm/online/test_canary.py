from datetime import datetime
from uuid import uuid4

import pytest
from app.agent.enums import AgentIntent
from app.agent.models import (
    AgentStateSummary,
    ComposeResponseInput,
    ComposeResponseResult,
    InterpretationNodeResult,
    InterpretMessageInput,
    InterpretMessageOutput,
    KnownIssueFields,
    NodeMetadata,
)
from app.agent_runtime.execution_context import bind_execution_context
from app.domain.enums import WorkflowStage
from app.llm.online.canary import (
    AllowlistedComposeNode,
    AllowlistedInterpretationNode,
    _presentation_contract,
)


def _interpretation(provider: str) -> InterpretationNodeResult:
    return InterpretationNodeResult(
        interpretation=InterpretMessageOutput(utterance_intent=AgentIntent.NEW_REPAIR),
        metadata=NodeMetadata(
            provider=provider,
            model=provider,
            prompt_name="interpret",
            prompt_version="1",
        ),
    )


def _input() -> InterpretMessageInput:
    return InterpretMessageInput(
        current_user_message="厨房水管接口漏水。",
        recent_conversation_messages=(),
        current_state_summary=AgentStateSummary(intent_version=1),
        current_workflow_stage=WorkflowStage.INTAKE,
        known_issue_fields=KnownIssueFields(),
        missing_fields=(),
        reference_time=datetime.fromisoformat("2026-08-01T12:00:00+08:00"),
        timezone_name="Asia/Shanghai",
    )


@pytest.mark.asyncio
async def test_authenticated_allowlisted_user_gets_online_interpretation() -> None:
    allowed_user = uuid4()

    async def scripted(_: InterpretMessageInput) -> InterpretationNodeResult:
        return _interpretation("scripted")

    async def online(_: InterpretMessageInput) -> InterpretationNodeResult:
        return _interpretation("deepseek")

    node = AllowlistedInterpretationNode(
        scripted=scripted,
        online=online,
        allowed_user_ids=frozenset({allowed_user}),
        enabled=True,
    )
    with bind_execution_context(uuid4(), uuid4(), uuid4(), None, user_id=allowed_user):
        result = await node(_input())
    assert result.metadata.provider == "deepseek"


@pytest.mark.asyncio
async def test_non_allowlisted_or_unbound_user_stays_scripted() -> None:
    allowed_user = uuid4()

    async def scripted(_: InterpretMessageInput) -> InterpretationNodeResult:
        return _interpretation("scripted")

    async def online(_: InterpretMessageInput) -> InterpretationNodeResult:
        raise AssertionError("online provider must not be reached")

    node = AllowlistedInterpretationNode(
        scripted=scripted,
        online=online,
        allowed_user_ids=frozenset({allowed_user}),
        enabled=True,
    )
    assert (await node(_input())).metadata.provider == "scripted"
    with bind_execution_context(uuid4(), uuid4(), uuid4(), None, user_id=uuid4()):
        assert (await node(_input())).metadata.provider == "scripted"


@pytest.mark.asyncio
async def test_grounded_canary_uses_same_authenticated_allowlist_boundary() -> None:
    allowed_user = uuid4()

    async def compose(provider: str) -> ComposeResponseResult:
        return ComposeResponseResult(
            response_text=provider,
            metadata=NodeMetadata(
                provider=provider,
                model=provider,
                prompt_name="compose",
                prompt_version="1",
            ),
        )

    async def scripted(_: ComposeResponseInput) -> ComposeResponseResult:
        return await compose("scripted")

    async def online(_: ComposeResponseInput) -> ComposeResponseResult:
        return await compose("deepseek")

    node = AllowlistedComposeNode(
        scripted=scripted,
        online=online,
        allowed_user_ids=frozenset({allowed_user}),
        enabled=True,
    )
    node_input = ComposeResponseInput(
        verified_business_facts=(),
        allowed_policy_evidence=(),
        current_workflow_stage=WorkflowStage.DONE,
    )
    with bind_execution_context(uuid4(), uuid4(), uuid4(), None, user_id=allowed_user):
        assert (await node(node_input)).metadata.provider == "deepseek"
    with bind_execution_context(uuid4(), uuid4(), uuid4(), None, user_id=uuid4()):
        assert (await node(node_input)).metadata.provider == "scripted"


def test_ticket_status_query_selects_noncritical_grounded_template() -> None:
    template_id, action, display_action = _presentation_contract(
        ComposeResponseInput(
            verified_business_facts=(),
            allowed_policy_evidence=(),
            current_workflow_stage=WorkflowStage.DONE,
            task_intent=AgentIntent.QUERY_TICKET_STATUS,
        )
    )

    assert template_id == "STATUS_UPDATE"
    assert action.value == "NONE"
    assert display_action is None

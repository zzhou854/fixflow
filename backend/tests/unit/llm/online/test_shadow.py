from __future__ import annotations

from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from app.agent.enums import AgentIntent
from app.agent.models import (
    AgentStateSummary,
    InterpretationNodeResult,
    InterpretMessageInput,
    InterpretMessageOutput,
    KnownIssueFields,
    NodeMetadata,
)
from app.agent_runtime.execution_context import bind_execution_context
from app.domain.enums import WorkflowStage
from app.llm.online.shadow import (
    ShadowingInterpretationNode,
    ShadowRunEvidence,
    SqlAlchemyShadowEvidenceSink,
)


def _result(provider: str, model: str) -> InterpretationNodeResult:
    return InterpretationNodeResult(
        interpretation=InterpretMessageOutput(utterance_intent=AgentIntent.NEW_REPAIR),
        metadata=NodeMetadata(
            provider=provider,
            model=model,
            prompt_name="resident_fact_extraction",
            prompt_version="2.0.0",
            schema_version="resident-facts-v2",
        ),
    )


def _input() -> InterpretMessageInput:
    return InterpretMessageInput(
        current_user_message="厨房漏水",
        recent_conversation_messages=(),
        current_state_summary=AgentStateSummary(intent_version=1),
        current_workflow_stage=WorkflowStage.INTAKE,
        known_issue_fields=KnownIssueFields(),
        missing_fields=(),
        reference_time=datetime.now(UTC),
        timezone_name="UTC",
    )


class Sink:
    def __init__(self) -> None:
        self.records: list[ShadowRunEvidence] = []

    async def append(self, evidence: ShadowRunEvidence) -> None:
        self.records.append(evidence)


@pytest.mark.asyncio
async def test_shadow_never_influences_primary_and_persists_only_hash() -> None:
    sink = Sink()

    async def primary(_: InterpretMessageInput) -> InterpretationNodeResult:
        return _result("scripted", "scripted")

    async def shadow(_: InterpretMessageInput) -> InterpretationNodeResult:
        return _result("deepseek", "deepseek-v4-flash")

    node = ShadowingInterpretationNode(
        primary=primary,
        shadow=shadow,
        sink=cast(SqlAlchemyShadowEvidenceSink, sink),
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_version="2.0.0",
        schema_version="resident-facts-v2",
    )
    run_id = uuid4()
    with bind_execution_context(run_id, uuid4(), uuid4(), None):
        result = await node(_input())
    await node.drain()
    assert result.metadata.provider == "scripted"
    assert len(sink.records) == 1
    evidence = sink.records[0]
    assert evidence.source_run_id == run_id
    assert evidence.structured_result_hash is not None
    assert len(evidence.structured_result_hash) == 64
    assert not hasattr(evidence, "raw_response")


@pytest.mark.asyncio
async def test_shadow_failure_is_isolated_and_classified() -> None:
    sink = Sink()

    async def primary(_: InterpretMessageInput) -> InterpretationNodeResult:
        return _result("scripted", "scripted")

    async def shadow(_: InterpretMessageInput) -> InterpretationNodeResult:
        raise RuntimeError("synthetic failure without source content")

    node = ShadowingInterpretationNode(
        primary=primary,
        shadow=shadow,
        sink=cast(SqlAlchemyShadowEvidenceSink, sink),
        provider="deepseek",
        model="deepseek-v4-flash",
        prompt_version="2.0.0",
        schema_version="resident-facts-v2",
    )
    with bind_execution_context(uuid4(), uuid4(), uuid4(), None):
        result = await node(_input())
    await node.drain()
    assert result.metadata.provider == "scripted"
    assert sink.records[0].error_code == "RuntimeError"
    assert sink.records[0].structured_result_hash is None

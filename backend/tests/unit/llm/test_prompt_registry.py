import json
from pathlib import Path

import pytest
from app.agent.enums import AgentIntent
from app.agent.models import (
    AgentStateSummary,
    InterpretMessageInput,
    KnownIssueFields,
)
from app.agent.nodes.interpret_message import InterpretMessageNode
from app.api.demo_providers import DemoScriptedLLMProvider
from app.domain.enums import WorkflowStage
from app.llm.prompts.registry import PromptRegistry


def test_registry_loads_versioned_assets_and_stable_hash() -> None:
    first = PromptRegistry().resident_interpretation()
    second = PromptRegistry().resident_interpretation()
    assert first.prompt_id == "resident_interpretation"
    assert first.prompt_version == "1.0.0"
    assert first.schema_version == "interpretation-result-v1"
    assert len(first.examples) == 16
    assert first.prompt_hash == second.prompt_hash
    assert len(first.prompt_hash) == 64


def test_registry_fails_fast_for_missing_or_invalid_assets(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        PromptRegistry(tmp_path).resident_interpretation()
    (tmp_path / "system_v1.md").write_text("system", encoding="utf-8")
    (tmp_path / "output_schema_v1.json").write_text("[]", encoding="utf-8")
    (tmp_path / "examples_v1.json").write_text(json.dumps([]), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        PromptRegistry(tmp_path).resident_interpretation()


def test_examples_are_validated_by_formal_interpretation_schema() -> None:
    prompt = PromptRegistry().resident_interpretation()
    assert all(example.output.utterance_intent for example in prompt.examples)
    assert all("api_key" not in example.model_dump_json().casefold() for example in prompt.examples)


def test_registry_supports_explicit_v2_without_changing_default() -> None:
    registry = PromptRegistry()
    default = registry.resident_interpretation()
    v1 = registry.resident_interpretation("1.0.0")
    v2 = registry.resident_interpretation("2.0.0")
    assert default is v1
    assert v1.prompt_hash == ("8c161de155a0b3bb42b00ba516e90fa5526912945126c88538c452171e1f94d9")
    assert v2.prompt_version == "2.0.0"
    assert v2.schema_version == v1.schema_version == "interpretation-result-v1"
    assert len(v2.examples) == 20
    assert v2.prompt_hash != v1.prompt_hash
    assert v2.prompt_hash == registry.resident_interpretation("2.0.0").prompt_hash


def test_registry_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        PromptRegistry().resident_interpretation("3.0.0")


@pytest.mark.asyncio
async def test_v2_fully_assembled_message_stays_within_provider_contract() -> None:
    class CapturingProvider(DemoScriptedLLMProvider):
        observed_length = 0

        async def generate_structured(self, **kwargs: object):  # type: ignore[no-untyped-def]
            messages = kwargs["messages"]
            self.observed_length = len(messages[0].content)  # type: ignore[index]
            return await super().generate_structured(**kwargs)  # type: ignore[arg-type]

    provider = CapturingProvider()
    node = InterpretMessageNode(
        provider,
        model="test",
        prompt_version="2.0.0",
    )
    await node(
        InterpretMessageInput(
            current_user_message="客厅灯不亮，请维修",
            recent_conversation_messages=(),
            current_state_summary=AgentStateSummary(
                task_intent=AgentIntent.NEW_REPAIR,
                intent_version=1,
            ),
            current_workflow_stage=WorkflowStage.INTAKE,
            known_issue_fields=KnownIssueFields(),
            missing_fields=(),
            reference_time="2032-01-01T09:00:00+08:00",
            timezone_name="Asia/Shanghai",
        )
    )
    assert provider.observed_length <= 12_000

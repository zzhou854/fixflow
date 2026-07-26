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


def test_registry_supports_v21_overlay_without_mutating_v2() -> None:
    registry = PromptRegistry()
    v2 = registry.resident_interpretation("2.0.0")
    candidate = registry.resident_interpretation("2.1.0")

    assert candidate.prompt_version == "2.1.0"
    assert len(candidate.examples) == 24
    assert candidate.prompt_hash != v2.prompt_hash
    assert candidate.prompt_hash == registry.resident_interpretation("2.1.0").prompt_hash
    assert len(candidate.system_template) > len(v2.system_template)


def test_registry_supports_v22_as_a_distinct_candidate() -> None:
    registry = PromptRegistry()
    v21 = registry.resident_interpretation("2.1.0")
    candidate = registry.resident_interpretation("2.2.0")

    assert candidate.prompt_version == "2.2.0"
    assert len(candidate.examples) == 24
    assert candidate.prompt_hash != v21.prompt_hash
    assert candidate.prompt_hash == registry.resident_interpretation("2.2.0").prompt_hash


def test_registry_supports_major_v3_decision_structure() -> None:
    registry = PromptRegistry()
    v22 = registry.resident_interpretation("2.2.0")
    candidate = registry.resident_interpretation("3.0.0")

    assert candidate.prompt_version == "3.0.0"
    assert len(candidate.examples) == 24
    assert candidate.prompt_hash != v22.prompt_hash
    assert candidate.prompt_hash == registry.resident_interpretation("3.0.0").prompt_hash


def test_registry_supports_v31_explicit_missing_field_examples() -> None:
    registry = PromptRegistry()
    v3 = registry.resident_interpretation("3.0.0")
    candidate = registry.resident_interpretation("3.1.0")

    assert candidate.prompt_version == "3.1.0"
    assert len(candidate.examples) == 24
    assert candidate.prompt_hash != v3.prompt_hash
    assert candidate.prompt_hash == registry.resident_interpretation("3.1.0").prompt_hash


def test_registry_supports_v32_mutually_exclusive_missing_field_rules() -> None:
    registry = PromptRegistry()
    v31 = registry.resident_interpretation("3.1.0")
    candidate = registry.resident_interpretation("3.2.0")

    assert candidate.prompt_version == "3.2.0"
    assert len(candidate.examples) == 24
    assert candidate.prompt_hash != v31.prompt_hash
    assert candidate.prompt_hash == registry.resident_interpretation("3.2.0").prompt_hash


@pytest.mark.parametrize(
    ("version", "expected_hash", "example_count"),
    [
        ("3.1.0", "205c9196304f72ee5a6e31a315cc98bbc8f2f62d9c0dcd5de66b97b66d0b6eb8", 24),
        ("3.2.0", "039081e7569f2dfd2da5202c18d979951bebde3445ba4ae49eff18f4fe387df0", 24),
        ("3.3.0", "2528064bafba2ab97c306b497c9e87d5b036245862bce7094395b4b226f87cad", 24),
        ("3.4.0", "89e854fd4f575dec7d410e17e9cc5fe536b3c02dabf2d16cf482fa147b7f3752", 24),
        ("3.5.0", "596df03ac8729969ebfae5947fea671a56b8cb0630d2f4dd81ecf29ae851df9b", 24),
        ("3.6.0", "ea649097aa707da5c7b52839004fa1c23b1d2b5467b9da1e6db219a2cbc11fac", 24),
        ("3.7.0", "85c46e4a72396c39a935d507a212d483cc98ac31a116388372cfc9ba7622adea", 24),
        ("4.0.0", "5c76909e373224ed7d7d15593a0f9196e9a60e82feccd9981983bd57b888f267", 20),
    ],
)
def test_experimental_candidate_assets_are_immutable(
    version: str,
    expected_hash: str,
    example_count: int,
) -> None:
    candidate = PromptRegistry().resident_interpretation(version)
    assert candidate.prompt_hash == expected_hash
    assert len(candidate.examples) == example_count


def test_registry_rejects_unknown_version() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        PromptRegistry().resident_interpretation("9.0.0")


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


@pytest.mark.asyncio
async def test_v21_fully_assembled_message_stays_within_provider_contract() -> None:
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
        prompt_version="2.1.0",
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


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["3.3.0", "3.4.0", "3.5.0", "3.6.0", "3.7.0", "4.0.0"])
async def test_later_candidates_stay_within_provider_contract(version: str) -> None:
    class CapturingProvider(DemoScriptedLLMProvider):
        observed_length = 0

        async def generate_structured(self, **kwargs: object):  # type: ignore[no-untyped-def]
            messages = kwargs["messages"]
            self.observed_length = len(messages[0].content)  # type: ignore[index]
            return await super().generate_structured(**kwargs)  # type: ignore[arg-type]

    provider = CapturingProvider()
    node = InterpretMessageNode(provider, model="test", prompt_version=version)
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

import json
from pathlib import Path

import pytest
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

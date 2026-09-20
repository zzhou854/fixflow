from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

from app.llm.evaluation.challenge import validate_holdout_isolation
from app.llm.evaluation.dataset_loader import load_dataset
from app.llm.evaluation.dataset_validator import DatasetValidator
from app.llm.hybrid.prompts import FactPromptRegistry
from app.llm.prompts.registry import PromptRegistry


def normalized(value: str) -> str:
    return "".join(value.casefold().split())


def test_architecture_3_holdout_is_locked_unique_and_unconsumed() -> None:
    root = Path("backend/evals/datasets")
    holdout = load_dataset(root / "resident_interpretation_holdout_v1.jsonl")
    references = tuple(
        load_dataset(path)
        for path in sorted(root.glob("resident_interpretation*.jsonl"))
        if "holdout" not in path.name
    )
    prompt = PromptRegistry().resident_interpretation()
    fact_prompt = FactPromptRegistry().resident_fact_extraction()

    DatasetValidator().validate(
        holdout,
        prompt=prompt,
        require_release_distribution=False,
    )
    validate_holdout_isolation(holdout, references=references, prompts=(prompt,))
    assert fact_prompt.prompt_version == "2.0.0"

    holdout_messages = tuple(normalized(case.input.current_user_message) for case in holdout.cases)
    reference_messages = tuple(
        normalized(case.input.current_user_message)
        for dataset in references
        for case in dataset.cases
    )
    assert len(holdout.cases) == 120
    assert len(set(holdout_messages)) == 120
    assert set(holdout_messages).isdisjoint(reference_messages)
    assert (
        max(
            SequenceMatcher(None, candidate, historical).ratio()
            for candidate in holdout_messages
            for historical in reference_messages
        )
        < 0.94
    )


def test_architecture_3_holdout_distribution_is_frozen() -> None:
    holdout = load_dataset(Path("backend/evals/datasets/resident_interpretation_holdout_v1.jsonl"))
    tags = Counter(tag for case in holdout.cases for tag in case.tags)

    assert tags["create"] == 10
    assert tags["generic-facility-failure"] == 8
    assert tags["booking"] == 8
    assert tags["slot-selection"] == 8
    assert tags["reschedule"] == 16
    assert tags["missing-availability"] == 8
    assert tags["request-human"] == 8
    assert tags["callback"] == 6
    assert tags["safety-single"] == 8
    assert tags["safety-multi"] == 8
    assert tags["safety-negation"] == 8
    assert tags["safety-hypothetical"] == 6
    assert tags["multi-turn-correction"] == 8
    assert tags["unsupported"] == 6
    assert tags["small-talk"] == 6
    assert tags["adversarial"] == 6
    assert sum(case.severity.value == "CRITICAL" for case in holdout.cases) == 22

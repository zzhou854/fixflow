from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from app.llm.evaluation.challenge import validate_challenge_isolation
from app.llm.evaluation.dataset_loader import load_dataset
from app.llm.evaluation.dataset_validator import DatasetValidator
from app.llm.prompts.registry import PromptRegistry


def _normalized(value: str) -> str:
    return "".join(value.casefold().split())


def test_challenge_v2_is_valid_and_isolated_without_online_evaluation() -> None:
    root = Path("backend/evals/datasets")
    regression = load_dataset(root / "resident_interpretation_v1.jsonl")
    challenge_v1 = load_dataset(root / "resident_interpretation_challenge_v1.jsonl")
    challenge_v2 = load_dataset(root / "resident_interpretation_challenge_v2.jsonl")
    prompt = PromptRegistry().resident_interpretation()

    DatasetValidator().validate(
        challenge_v2,
        prompt=prompt,
        require_release_distribution=False,
    )
    validate_challenge_isolation(
        challenge_v2,
        regression=regression,
        prompts=(prompt,),
        expected_version="2.0.0",
    )

    old_messages = {_normalized(case.input.current_user_message) for case in challenge_v1.cases}
    new_messages = {_normalized(case.input.current_user_message) for case in challenge_v2.cases}
    assert len(new_messages) == 60
    assert old_messages.isdisjoint(new_messages)
    assert (
        max(SequenceMatcher(None, new, old).ratio() for new in new_messages for old in old_messages)
        < 0.94
    )

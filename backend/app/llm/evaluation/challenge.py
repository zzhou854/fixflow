"""Locked challenge-corpus validation and development-time online guard."""

from __future__ import annotations

from difflib import SequenceMatcher

from app.llm.evaluation.errors import DatasetValidationError, OnlineGuardError
from app.llm.evaluation.models import EvaluationDataset
from app.llm.prompts.registry import PromptDefinition

CHALLENGE_DATASET_ID = "resident_interpretation_challenge"
CHALLENGE_DATASET_VERSION = "1.0.0"
HOLDOUT_DATASET_ID = "resident_interpretation_holdout"
HOLDOUT_DATASET_VERSION = "1.0.0"


def validate_challenge_isolation(
    challenge: EvaluationDataset,
    *,
    regression: EvaluationDataset,
    prompts: tuple[PromptDefinition, ...],
    expected_version: str = CHALLENGE_DATASET_VERSION,
) -> None:
    if challenge.metadata.dataset_id != CHALLENGE_DATASET_ID:
        raise DatasetValidationError("challenge dataset identity mismatch")
    if challenge.metadata.dataset_version != expected_version:
        raise DatasetValidationError("challenge dataset version mismatch")
    challenge_messages = {_normalized(case.input.current_user_message) for case in challenge.cases}
    regression_messages = {
        _normalized(case.input.current_user_message) for case in regression.cases
    }
    example_messages = {
        _normalized(
            str(example.input.get("current_user_message", example.input.get("current_message", "")))
        )
        for prompt in prompts
        for example in prompt.examples
    }
    if challenge_messages & regression_messages:
        raise DatasetValidationError("challenge dataset duplicates regression messages")
    if challenge_messages & example_messages:
        raise DatasetValidationError("challenge dataset duplicates prompt examples")
    if len(challenge_messages) != len(challenge.cases):
        raise DatasetValidationError("challenge dataset contains duplicate messages")
    reference = regression_messages | example_messages
    suspicious = [
        message
        for message in challenge_messages
        if any(SequenceMatcher(None, message, other).ratio() >= 0.94 for other in reference)
    ]
    if suspicious:
        raise DatasetValidationError("challenge dataset contains highly similar source templates")


def validate_holdout_isolation(
    holdout: EvaluationDataset,
    *,
    references: tuple[EvaluationDataset, ...],
    prompts: tuple[PromptDefinition, ...],
) -> None:
    """Validate the single Architecture 3.0 holdout before its first live call."""

    if holdout.metadata.dataset_id != HOLDOUT_DATASET_ID:
        raise DatasetValidationError("holdout dataset identity mismatch")
    if holdout.metadata.dataset_version != HOLDOUT_DATASET_VERSION:
        raise DatasetValidationError("holdout dataset version mismatch")
    if holdout.metadata.provenance != "synthetic_engineering_holdout":
        raise DatasetValidationError("holdout provenance mismatch")
    if holdout.metadata.review_status != "engineering_authored_locked":
        raise DatasetValidationError("holdout must be locked before use")

    holdout_messages = {_normalized(case.input.current_user_message) for case in holdout.cases}
    reference_messages = {
        _normalized(case.input.current_user_message)
        for dataset in references
        for case in dataset.cases
    }
    example_messages = {
        _normalized(
            str(example.input.get("current_user_message", example.input.get("current_message", "")))
        )
        for prompt in prompts
        for example in prompt.examples
    }
    if len(holdout_messages) != len(holdout.cases):
        raise DatasetValidationError("holdout contains duplicate messages")
    if holdout_messages & reference_messages:
        raise DatasetValidationError("holdout duplicates a historical corpus")
    if holdout_messages & example_messages:
        raise DatasetValidationError("holdout duplicates a prompt example")


def reject_challenge_for_prompt_development(dataset_id: str, *, live_network: bool) -> None:
    if live_network and dataset_id == CHALLENGE_DATASET_ID:
        raise OnlineGuardError(
            "locked challenge corpus cannot be used by prompt-development online evaluation"
        )


def _normalized(value: str) -> str:
    return "".join(value.casefold().split())

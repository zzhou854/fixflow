import json
from collections import Counter
from pathlib import Path

import pytest
from app.llm.evaluation.dataset_loader import load_dataset
from app.llm.evaluation.dataset_validator import DatasetValidator, validate_policy
from app.llm.evaluation.errors import DatasetValidationError
from app.llm.evaluation.hashing import dataset_hash
from app.llm.evaluation.models import (
    EvaluationDataset,
    EvaluationMatcher,
    EvaluationSeverity,
    EvaluationSuite,
)
from app.llm.prompts.registry import PromptRegistry


def test_release_dataset_is_valid(
    release_dataset: EvaluationDataset,
    gate_policy: object,
) -> None:
    DatasetValidator().validate(
        release_dataset,
        prompt=PromptRegistry().resident_interpretation(),
    )
    validate_policy(gate_policy)  # type: ignore[arg-type]


def test_release_dataset_identity(release_dataset: EvaluationDataset) -> None:
    assert release_dataset.metadata.dataset_id == "resident_interpretation"
    assert release_dataset.metadata.dataset_version == "1.0.0"
    assert release_dataset.metadata.dataset_schema_version == "evaluation-case-v1"
    assert release_dataset.metadata.contains_real_personal_data is False
    assert release_dataset.metadata.provenance == "synthetic_engineering_fixture"
    assert release_dataset.metadata.review_status == "engineering_authored"


def test_release_dataset_distribution(release_dataset: EvaluationDataset) -> None:
    assert Counter(case.suite for case in release_dataset.cases) == {
        EvaluationSuite.CREATE_TICKET: 18,
        EvaluationSuite.NEED_INFORMATION: 18,
        EvaluationSuite.BOOK_APPOINTMENT: 14,
        EvaluationSuite.RESCHEDULE_APPOINTMENT: 12,
        EvaluationSuite.REQUEST_HUMAN: 10,
        EvaluationSuite.SAFETY: 16,
        EvaluationSuite.SMALL_TALK: 10,
        EvaluationSuite.UNSUPPORTED: 8,
        EvaluationSuite.ADVERSARIAL: 8,
        EvaluationSuite.MULTI_TURN_CORRECTION: 6,
    }
    assert sum(case.severity is EvaluationSeverity.CRITICAL for case in release_dataset.cases) == 16
    assert (
        sum(bool(case.input.recent_conversation_messages) for case in release_dataset.cases) >= 18
    )
    assert sum("prompt-injection" in case.tags for case in release_dataset.cases) == 9


def test_dataset_hash_matches_canonical_content(release_dataset: EvaluationDataset) -> None:
    assert dataset_hash(release_dataset.metadata, release_dataset.cases) == (
        "a0dfb91aed5653eafcb5649beee1f9106ac4d6b332df50923c68d5f02e979296"
    )


def test_dataset_hash_ignores_manifest_hash_field(release_dataset: EvaluationDataset) -> None:
    changed = release_dataset.metadata.model_copy(update={"dataset_hash": "f" * 64})
    assert dataset_hash(changed, release_dataset.cases) == release_dataset.metadata.dataset_hash


def test_duplicate_case_id_is_rejected(release_dataset: EvaluationDataset) -> None:
    changed = release_dataset.model_copy(
        update={"cases": (*release_dataset.cases, release_dataset.cases[0])}
    )
    with pytest.raises(DatasetValidationError, match="unique"):
        DatasetValidator().validate(
            changed,
            prompt=PromptRegistry().resident_interpretation(),
            require_release_distribution=False,
        )


def test_manifest_count_mismatch_is_rejected(release_dataset: EvaluationDataset) -> None:
    changed = release_dataset.model_copy(
        update={"metadata": release_dataset.metadata.model_copy(update={"case_count": 119})}
    )
    with pytest.raises(DatasetValidationError, match="case_count"):
        DatasetValidator().validate(
            changed,
            prompt=PromptRegistry().resident_interpretation(),
            require_release_distribution=False,
        )


def test_hash_mismatch_is_rejected(release_dataset: EvaluationDataset) -> None:
    changed = release_dataset.model_copy(
        update={"metadata": release_dataset.metadata.model_copy(update={"dataset_hash": "f" * 64})}
    )
    with pytest.raises(DatasetValidationError, match="dataset_hash"):
        DatasetValidator().validate(
            changed,
            prompt=PromptRegistry().resident_interpretation(),
            require_release_distribution=False,
        )


def test_blank_jsonl_line_is_rejected(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
) -> None:
    case_path = tmp_path / "resident_interpretation_v1.jsonl"
    case_path.write_text(
        release_dataset.cases[0].model_dump_json() + "\n\n",
        encoding="utf-8",
    )
    manifest = release_dataset.metadata.model_copy(
        update={"case_count": 1, "dataset_hash": "0" * 64}
    )
    case_path.with_suffix(".manifest.json").write_text(
        manifest.model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(DatasetValidationError, match="blank"):
        load_dataset(case_path)


def test_invalid_json_line_is_rejected(
    tmp_path: Path,
    release_dataset: EvaluationDataset,
) -> None:
    case_path = tmp_path / "resident_interpretation_v1.jsonl"
    case_path.write_text("{broken\n", encoding="utf-8")
    case_path.with_suffix(".manifest.json").write_text(
        release_dataset.metadata.model_dump_json(),
        encoding="utf-8",
    )
    with pytest.raises(DatasetValidationError, match="line 1"):
        load_dataset(case_path)


@pytest.mark.parametrize(
    "sensitive",
    [
        "联系手机号 13912345678",
        "邮箱 resident@example.com",
        "Authorization Bearer abcdefghijk",
        "GLM_API_KEY=abcdefghijk",
        "数据库 postgresql://user:secret@host/db",
    ],
)
def test_sensitive_synthetic_input_is_rejected(
    release_dataset: EvaluationDataset,
    sensitive: str,
) -> None:
    case = release_dataset.cases[0].model_copy(
        update={
            "input": release_dataset.cases[0].input.model_copy(
                update={"current_user_message": sensitive}
            )
        }
    )
    changed = release_dataset.model_copy(
        update={
            "cases": (case,),
            "metadata": release_dataset.metadata.model_copy(
                update={"case_count": 1, "dataset_hash": "0" * 64}
            ),
        }
    )
    changed = changed.model_copy(
        update={
            "metadata": changed.metadata.model_copy(
                update={"dataset_hash": dataset_hash(changed.metadata, changed.cases)}
            )
        }
    )
    with pytest.raises(DatasetValidationError, match="possible"):
        DatasetValidator().validate(
            changed,
            prompt=PromptRegistry().resident_interpretation(),
            require_release_distribution=False,
        )


def test_prompt_example_exact_duplicate_is_rejected(
    release_dataset: EvaluationDataset,
) -> None:
    case = release_dataset.cases[0].model_copy(
        update={
            "input": release_dataset.cases[0].input.model_copy(
                update={"current_user_message": "厨房水管一直漏水"}
            )
        }
    )
    changed = release_dataset.model_copy(
        update={
            "cases": (case,),
            "metadata": release_dataset.metadata.model_copy(
                update={"case_count": 1, "dataset_hash": "0" * 64}
            ),
        }
    )
    changed = changed.model_copy(
        update={
            "metadata": changed.metadata.model_copy(
                update={"dataset_hash": dataset_hash(changed.metadata, changed.cases)}
            )
        }
    )
    with pytest.raises(DatasetValidationError, match="prompt example"):
        DatasetValidator().validate(
            changed,
            prompt=PromptRegistry().resident_interpretation(),
            require_release_distribution=False,
        )


@pytest.mark.parametrize(
    ("pattern", "message"),
    [
        ("[", "invalid regular expression"),
        ("a" * 257, "exceeds 256 characters"),
    ],
)
def test_invalid_or_oversized_regex_is_rejected_by_dataset_validator(
    release_dataset: EvaluationDataset,
    pattern: str,
    message: str,
) -> None:
    original = release_dataset.cases[0]
    regex_field = original.expected.fields[0].model_copy(
        update={
            "path": "$.issue_description_update",
            "matcher": EvaluationMatcher.REGEX,
            "value": pattern,
        }
    )
    case = original.model_copy(
        update={
            "expected": original.expected.model_copy(
                update={"fields": (regex_field, *original.expected.fields[1:])}
            )
        }
    )
    changed = release_dataset.model_copy(
        update={
            "cases": (case,),
            "metadata": release_dataset.metadata.model_copy(
                update={"case_count": 1, "dataset_hash": "0" * 64}
            ),
        }
    )
    changed = changed.model_copy(
        update={
            "metadata": changed.metadata.model_copy(
                update={"dataset_hash": dataset_hash(changed.metadata, changed.cases)}
            )
        }
    )
    with pytest.raises(DatasetValidationError, match=message):
        DatasetValidator().validate(
            changed,
            prompt=PromptRegistry().resident_interpretation(),
            require_release_distribution=False,
        )


def test_case_json_has_no_extra_fields(release_dataset: EvaluationDataset) -> None:
    raw = json.loads(release_dataset.cases[0].model_dump_json())
    raw["unexpected"] = True
    with pytest.raises(ValueError):
        type(release_dataset.cases[0]).model_validate(raw)

"""Strict JSON/JSONL loading with line-local diagnostics."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.llm.evaluation.errors import DatasetValidationError, PolicyValidationError
from app.llm.evaluation.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationDatasetMetadata,
    EvaluationGatePolicy,
)


def load_dataset(case_path: Path, manifest_path: Path | None = None) -> EvaluationDataset:
    manifest_file = manifest_path or case_path.with_suffix(".manifest.json")
    try:
        metadata = EvaluationDatasetMetadata.model_validate_json(
            manifest_file.read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, ValidationError) as exc:
        raise DatasetValidationError(f"invalid dataset manifest: {manifest_file.name}") from exc
    cases: list[EvaluationCase] = []
    try:
        with case_path.open("r", encoding="utf-8", newline="") as source:
            if source.read(1) == "\ufeff":
                raise DatasetValidationError("dataset must not contain UTF-8 BOM")
            source.seek(0)
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise DatasetValidationError(
                        f"blank JSONL line is not allowed at line {line_number}"
                    )
                try:
                    cases.append(EvaluationCase.model_validate_json(line))
                except ValidationError as exc:
                    raise DatasetValidationError(
                        f"invalid evaluation case at line {line_number}"
                    ) from exc
    except DatasetValidationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise DatasetValidationError(f"cannot read dataset: {case_path.name}") from exc
    return EvaluationDataset(metadata=metadata, cases=tuple(cases))


def load_policy(path: Path) -> EvaluationGatePolicy:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return EvaluationGatePolicy.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise PolicyValidationError(f"invalid evaluation policy: {path.name}") from exc

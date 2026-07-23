"""Canonical, path-independent SHA-256 identities."""

import hashlib
import json
from collections.abc import Mapping, Sequence

from app.llm.evaluation.models import (
    EvaluationCase,
    EvaluationDatasetMetadata,
    EvaluationGatePolicy,
)


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_value(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_text(value: str) -> str:
    """Hash the exact UTF-8 bytes of a persisted text artifact."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def dataset_hash(
    metadata: EvaluationDatasetMetadata | Mapping[str, object],
    cases: Sequence[EvaluationCase],
) -> str:
    raw = (
        metadata.model_dump(mode="json")
        if isinstance(metadata, EvaluationDatasetMetadata)
        else dict(metadata)
    )
    raw.pop("dataset_hash", None)
    return sha256_value(
        {
            "metadata": raw,
            "cases": [case.model_dump(mode="json") for case in cases],
        }
    )


def policy_hash(policy: EvaluationGatePolicy | Mapping[str, object]) -> str:
    raw = (
        policy.model_dump(mode="json") if isinstance(policy, EvaluationGatePolicy) else dict(policy)
    )
    raw.pop("policy_hash", None)
    return sha256_value(raw)

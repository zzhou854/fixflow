"""Typed, metadata-blind projections for private qualification records."""

from __future__ import annotations

import hashlib
import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field


class StructuredQualificationRecord(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    input: dict[str, object] = Field(default_factory=dict)


class GroundedQualificationRecord(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    input: dict[str, object] = Field(default_factory=dict)


def project_structured_provider_input(
    record: StructuredQualificationRecord,
) -> dict[str, object]:
    """Return only the model-visible Structured input, never evaluation metadata."""

    return _detached_input(record)


def project_grounded_provider_input(
    record: GroundedQualificationRecord,
) -> dict[str, object]:
    """Return only the model-visible Grounded input, never comparison metadata."""

    return _detached_input(record)


def provider_input_hash(value: dict[str, object]) -> str:
    """Hash only the projected request for audit-safe identity comparison."""

    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _detached_input(
    record: StructuredQualificationRecord | GroundedQualificationRecord,
) -> dict[str, object]:
    return cast(dict[str, object], json.loads(json.dumps(record.input, ensure_ascii=False)))

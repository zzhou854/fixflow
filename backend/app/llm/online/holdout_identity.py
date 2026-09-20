"""Frozen source identities used by the sealed Holdout manifests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.llm.hybrid.models import ExtractedResidentFactsV2
from app.llm.hybrid.prompts import FactPromptRegistry
from app.llm.online.grounded import (
    GROUNDED_PROMPT_TEMPLATE,
    GROUNDED_PROMPT_VERSION,
    GROUNDED_SCHEMA_VERSION,
    GroundedResponseDraft,
)
from app.llm.online.holdout_protocol import FrozenQualificationIdentity, sha256_file
from app.llm.online.routing import PROVIDER_ROUTER_VERSION

STRUCTURED_NORMALIZATION_VERSION = "hybrid-normalization-v1"
GROUNDED_NORMALIZATION_VERSION = "grounded-template-renderer-v1"

_BACKEND_ROOT = Path(__file__).parents[3]
_REPOSITORY_ROOT = _BACKEND_ROOT.parent
_HOLDOUT_SPECS = _BACKEND_ROOT / "evals" / "holdout"


def structured_identity(*, code_commit: str) -> FrozenQualificationIdentity:
    prompt = FactPromptRegistry().resident_fact_extraction()
    return FrozenQualificationIdentity(
        code_commit=code_commit,
        prompt_version=f"{prompt.prompt_id}@{prompt.prompt_version}",
        prompt_sha256=prompt.prompt_hash,
        schema_version=prompt.schema_version,
        schema_sha256=_canonical_hash(
            ExtractedResidentFactsV2.model_json_schema(mode="validation")
        ),
        normalization_version=STRUCTURED_NORMALIZATION_VERSION,
        normalization_sha256=_source_bundle_hash(
            (
                _BACKEND_ROOT / "app" / "llm" / "hybrid" / "normalizer.py",
                _BACKEND_ROOT / "app" / "llm" / "hybrid" / "decision.py",
                _BACKEND_ROOT / "app" / "llm" / "hybrid" / "safety.py",
            )
        ),
        scorer_version="resident_interpretation_holdout_scorer@1.0.0",
        scorer_sha256=sha256_file(_HOLDOUT_SPECS / "structured_scorer_v1.json"),
        gate_version="resident_interpretation_holdout_gate@1.0.0",
        gate_sha256=sha256_file(_HOLDOUT_SPECS / "structured_gate_v1.json"),
        provider_router_version=PROVIDER_ROUTER_VERSION,
    )


def grounded_identity(*, code_commit: str) -> FrozenQualificationIdentity:
    return FrozenQualificationIdentity(
        code_commit=code_commit,
        prompt_version=f"grounded_response@{GROUNDED_PROMPT_VERSION}",
        prompt_sha256=hashlib.sha256(GROUNDED_PROMPT_TEMPLATE.encode("utf-8")).hexdigest(),
        schema_version=GROUNDED_SCHEMA_VERSION,
        schema_sha256=_canonical_hash(GroundedResponseDraft.model_json_schema(mode="validation")),
        normalization_version=GROUNDED_NORMALIZATION_VERSION,
        normalization_sha256=_source_bundle_hash(
            (_BACKEND_ROOT / "app" / "llm" / "online" / "grounded.py",)
        ),
        scorer_version="grounded_response_holdout_scorer@1.0.0",
        scorer_sha256=sha256_file(_HOLDOUT_SPECS / "grounded_scorer_v1.json"),
        gate_version="grounded_response_holdout_gate@1.0.0",
        gate_sha256=sha256_file(_HOLDOUT_SPECS / "grounded_gate_v1.json"),
        provider_router_version=PROVIDER_ROUTER_VERSION,
    )


def revised_structured_identity(*, code_commit: str) -> FrozenQualificationIdentity:
    """Identity for the independently reviewed 2.1.0 structured package."""

    original = structured_identity(code_commit=code_commit)
    return original.model_copy(
        update={
            "scorer_version": "resident_interpretation_holdout_scorer@1.1.0",
            "scorer_sha256": sha256_file(_HOLDOUT_SPECS / "structured_scorer_v1_1.json"),
            "gate_version": "resident_interpretation_holdout_gate@1.1.0",
            "gate_sha256": sha256_file(_HOLDOUT_SPECS / "structured_gate_v1_1.json"),
        }
    )


def revised_grounded_identity(*, code_commit: str) -> FrozenQualificationIdentity:
    """Identity for the independently reviewed 1.1.0 grounded package."""

    original = grounded_identity(code_commit=code_commit)
    return original.model_copy(
        update={
            "scorer_version": "grounded_response_holdout_scorer@1.1.0",
            "scorer_sha256": sha256_file(_HOLDOUT_SPECS / "grounded_scorer_v1_1.json"),
            "gate_version": "grounded_response_holdout_gate@1.1.0",
            "gate_sha256": sha256_file(_HOLDOUT_SPECS / "grounded_gate_v1_1.json"),
        }
    )


def revised_grounded_identity_v1_2(*, code_commit: str) -> FrozenQualificationIdentity:
    """Identity for the expanded deterministic/natural 1.2.0 grounded package."""

    original = grounded_identity(code_commit=code_commit)
    return original.model_copy(
        update={
            "scorer_version": "grounded_response_holdout_scorer@1.2.0",
            "scorer_sha256": sha256_file(_HOLDOUT_SPECS / "grounded_scorer_v1_2.json"),
            "gate_version": "grounded_response_holdout_gate@1.2.0",
            "gate_sha256": sha256_file(_HOLDOUT_SPECS / "grounded_gate_v1_2.json"),
        }
    )


def _canonical_hash(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_bundle_hash(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        relative = path.relative_to(_REPOSITORY_ROOT).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


__all__ = [
    "GROUNDED_NORMALIZATION_VERSION",
    "STRUCTURED_NORMALIZATION_VERSION",
    "grounded_identity",
    "revised_grounded_identity",
    "revised_grounded_identity_v1_2",
    "revised_structured_identity",
    "structured_identity",
]

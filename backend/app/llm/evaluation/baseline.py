"""Atomic, offline publication of qualified model evidence."""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from app.llm.evaluation.artifacts import atomic_write_text
from app.llm.evaluation.errors import ArtifactError
from app.llm.evaluation.hashing import sha256_text, sha256_value
from app.llm.evaluation.models import (
    CriticalFailureCode,
    EvaluationCaseResult,
    EvaluationGateResult,
    EvaluationModel,
    EvaluationReport,
    EvaluationUsage,
    GitCommit,
    Sha256,
    Version,
)
from app.llm.evaluation.qualification import (
    QualificationDecision,
    QualificationStatus,
)

BASELINE_ID: Literal["resident-interpretation-glm-5-1-baseline-1"] = (
    "resident-interpretation-glm-5-1-baseline-1"
)
BASELINE_VERSION: Literal["1.0.0"] = "1.0.0"
RELEASE_CANDIDATE_ID: Literal["glm-5-1-resident-interpretation-rc1"] = (
    "glm-5-1-resident-interpretation-rc1"
)
RELEASE_CANDIDATE_VERSION: Literal["1.0.0"] = "1.0.0"

BASELINE_FILES = frozenset(
    {
        "baseline-manifest.json",
        "qualification-summary.json",
        "gate-result.json",
        "stability-result.json",
        "case-outcomes.jsonl",
        "README.md",
        "checksums.json",
    }
)

KNOWN_LIMITATIONS = (
    "Dataset is synthetic engineering data.",
    "Dataset is not an independently authored blind test.",
    "No real resident traffic was evaluated.",
    "No production SLA was validated.",
    "No production cost gate was evaluated.",
    "No long-term drift was evaluated.",
    "Production traffic was not activated.",
)


class BaselineStatus(StrEnum):
    QUALIFIED = "QUALIFIED"


class ReleaseCandidateStatus(StrEnum):
    QUALIFIED_CANDIDATE = "QUALIFIED_CANDIDATE"


class ActivationStatus(StrEnum):
    NOT_ACTIVATED = "NOT_ACTIVATED"


class EvidenceChecksum(EvaluationModel):
    relative_path: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}$")
    sha256: Sha256
    size_bytes: int = Field(ge=0)


class EvidenceChecksumIndex(EvaluationModel):
    checksum_schema_version: Literal["model-evidence-checksums-v1"] = "model-evidence-checksums-v1"
    files: tuple[EvidenceChecksum, ...] = Field(min_length=1)


class SourceArtifactHash(EvaluationModel):
    relative_path: Literal[
        "artifact-index.json",
        "summary.json",
        "gate-result.json",
    ]
    sha256: Sha256


class SafeCaseOutcome(EvaluationModel):
    case_id: str = Field(min_length=1, max_length=80)
    repeat_index: int = Field(ge=0, le=4)
    suite: str = Field(min_length=1, max_length=50)
    status: str = Field(min_length=1, max_length=50)
    case_passed: bool
    interpretation_fingerprint: Sha256 | None = None
    intent: str | None = Field(default=None, max_length=50)
    clarification_needed: bool | None = None
    missing_fields: tuple[str, ...] = ()
    safety_signals: tuple[str, ...] = ()
    critical_failure_codes: tuple[CriticalFailureCode, ...] = ()
    provider_error_code: str | None = Field(default=None, max_length=100)
    attempt_count: int = Field(ge=1, le=5)
    latency_ms: int = Field(ge=0)
    usage: EvaluationUsage


class ModelBaselineManifest(EvaluationModel):
    baseline_id: Literal["resident-interpretation-glm-5-1-baseline-1"] = BASELINE_ID
    baseline_version: Literal["1.0.0"] = BASELINE_VERSION
    baseline_schema_version: Literal["model-baseline-v1"] = "model-baseline-v1"
    status: BaselineStatus = BaselineStatus.QUALIFIED
    created_at_utc: datetime
    source_run_id: str = Field(min_length=36, max_length=36)
    qualified_runtime_commit: GitCommit
    dataset_id: str
    dataset_version: Version
    dataset_hash: Sha256
    provider: Literal["zai"]
    model: Literal["glm-5.1"]
    provider_sdk: Literal["zai-sdk"]
    provider_sdk_version: Literal["0.2.3"]
    prompt_id: str
    prompt_version: Version
    prompt_hash: Sha256
    interpretation_schema_version: str
    scorer_id: str
    scorer_version: Version
    policy_id: str
    policy_version: Version
    policy_hash: Sha256
    settings_fingerprint: Sha256
    endpoint_fingerprint: Sha256
    repeat_count: Literal[2]
    evaluation_count: Literal[240]
    absolute_gate_passed: Literal[True]
    critical_gate_passed: Literal[True]
    stability_gate_passed: Literal[True]
    relative_gate_evaluated: Literal[False]
    baseline_eligible: Literal[True]
    artifact_hashes: tuple[SourceArtifactHash, ...] = Field(min_length=3, max_length=3)

    @field_validator("created_at_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at_utc must be timezone-aware")
        return value


class ModelReleaseCandidateManifest(EvaluationModel):
    release_candidate_id: Literal["glm-5-1-resident-interpretation-rc1"] = RELEASE_CANDIDATE_ID
    release_candidate_version: Literal["1.0.0"] = RELEASE_CANDIDATE_VERSION
    release_candidate_schema_version: Literal["model-release-candidate-v1"] = (
        "model-release-candidate-v1"
    )
    status: ReleaseCandidateStatus = ReleaseCandidateStatus.QUALIFIED_CANDIDATE
    activation_status: ActivationStatus = ActivationStatus.NOT_ACTIVATED
    baseline_id: Literal["resident-interpretation-glm-5-1-baseline-1"]
    baseline_version: Literal["1.0.0"]
    qualified_runtime_commit: GitCommit
    provider: Literal["zai"]
    model: Literal["glm-5.1"]
    prompt_id: str
    prompt_version: Version
    prompt_hash: Sha256
    dataset_id: str
    dataset_version: Version
    dataset_hash: Sha256
    policy_id: str
    policy_version: Version
    policy_hash: Sha256
    qualification_status: Literal["QUALIFIED"]
    known_limitations: tuple[str, ...] = Field(min_length=7)
    created_at_utc: datetime

    @field_validator("created_at_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at_utc must be timezone-aware")
        return value


def publish_baseline(
    report: EvaluationReport,
    results: tuple[EvaluationCaseResult, ...],
    decision: QualificationDecision,
    *,
    output_directory: Path,
    allowed_root: Path,
) -> ModelBaselineManifest:
    """Publish a qualified package atomically or verify an identical existing one."""
    if decision.status is not QualificationStatus.QUALIFIED or not decision.baseline_eligible:
        raise ArtifactError("qualification did not permit baseline publication")
    _require_within(output_directory, allowed_root)
    if report.gate_result is None:
        raise ArtifactError("gate result is required")
    completed_at = report.manifest.completed_at_utc
    if completed_at is None:
        raise ArtifactError("completed run timestamp is required")
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_directory.name}.", dir=output_directory.parent)
    )
    try:
        manifest = _write_baseline_files(
            temporary,
            report=report,
            results=results,
            decision=decision,
            gate=report.gate_result,
            completed_at=completed_at,
        )
        if output_directory.exists():
            _assert_identical_directories(temporary, output_directory)
            return ModelBaselineManifest.model_validate_json(
                (output_directory / "baseline-manifest.json").read_text(encoding="utf-8")
            )
        os.replace(temporary, output_directory)
        return manifest
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def publish_release_candidate(
    baseline_directory: Path,
    *,
    output_path: Path,
    allowed_root: Path,
) -> ModelReleaseCandidateManifest:
    """Publish a non-active candidate only from a verified Baseline package."""
    _require_within(output_path, allowed_root)
    baseline = verify_baseline(baseline_directory)
    candidate = ModelReleaseCandidateManifest(
        baseline_id=baseline.baseline_id,
        baseline_version=baseline.baseline_version,
        qualified_runtime_commit=baseline.qualified_runtime_commit,
        provider=baseline.provider,
        model=baseline.model,
        prompt_id=baseline.prompt_id,
        prompt_version=baseline.prompt_version,
        prompt_hash=baseline.prompt_hash,
        dataset_id=baseline.dataset_id,
        dataset_version=baseline.dataset_version,
        dataset_hash=baseline.dataset_hash,
        policy_id=baseline.policy_id,
        policy_version=baseline.policy_version,
        policy_hash=baseline.policy_hash,
        qualification_status="QUALIFIED",
        known_limitations=KNOWN_LIMITATIONS,
        created_at_utc=baseline.created_at_utc,
    )
    content = candidate.model_dump_json(indent=2) + "\n"
    if output_path.exists():
        if output_path.read_text(encoding="utf-8") != content:
            raise ArtifactError("release candidate exists with different content")
        return candidate
    output_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(output_path, content)
    return candidate


def verify_baseline(directory: Path) -> ModelBaselineManifest:
    expected = BASELINE_FILES
    actual = {path.name for path in directory.iterdir() if path.is_file()}
    if actual != expected:
        raise ArtifactError("baseline package file set mismatch")
    index = EvidenceChecksumIndex.model_validate_json(
        (directory / "checksums.json").read_text(encoding="utf-8")
    )
    indexed = {item.relative_path for item in index.files}
    if indexed != expected - {"checksums.json"}:
        raise ArtifactError("baseline checksum file set mismatch")
    for item in index.files:
        if Path(item.relative_path).name != item.relative_path:
            raise ArtifactError("baseline checksum contains unsafe path")
        path = directory / item.relative_path
        content = path.read_text(encoding="utf-8")
        if len(content.encode("utf-8")) != item.size_bytes:
            raise ArtifactError("baseline file size mismatch")
        if sha256_text(content) != item.sha256:
            raise ArtifactError("baseline checksum mismatch")
    return ModelBaselineManifest.model_validate_json(
        (directory / "baseline-manifest.json").read_text(encoding="utf-8")
    )


def _write_baseline_files(
    directory: Path,
    *,
    report: EvaluationReport,
    results: tuple[EvaluationCaseResult, ...],
    decision: QualificationDecision,
    gate: EvaluationGateResult,
    completed_at: datetime,
) -> ModelBaselineManifest:
    manifest = _baseline_manifest(
        report,
        decision,
        completed_at=completed_at,
        artifact_hashes=_source_hashes(decision),
    )
    atomic_write_text(
        directory / "baseline-manifest.json",
        manifest.model_dump_json(indent=2) + "\n",
    )
    atomic_write_text(
        directory / "qualification-summary.json",
        decision.model_dump_json(indent=2) + "\n",
    )
    atomic_write_text(directory / "gate-result.json", gate.model_dump_json(indent=2) + "\n")
    atomic_write_text(
        directory / "stability-result.json",
        decision.stability.model_dump_json(indent=2) + "\n",
    )
    outcomes = "".join(_safe_outcome(result).model_dump_json() + "\n" for result in results)
    atomic_write_text(directory / "case-outcomes.jsonl", outcomes)
    atomic_write_text(
        directory / "README.md",
        "# Qualified model baseline\n\n"
        "Synthetic evaluation evidence only; this is not production activation.\n",
    )
    checksums = _checksums(directory, BASELINE_FILES - {"checksums.json"})
    atomic_write_text(
        directory / "checksums.json",
        EvidenceChecksumIndex(files=checksums).model_dump_json(indent=2) + "\n",
    )
    verify_baseline(directory)
    return manifest


def _baseline_manifest(
    report: EvaluationReport,
    decision: QualificationDecision,
    *,
    completed_at: datetime,
    artifact_hashes: tuple[SourceArtifactHash, ...],
) -> ModelBaselineManifest:
    manifest = report.manifest
    provider = manifest.provider_configuration
    return ModelBaselineManifest(
        created_at_utc=completed_at,
        source_run_id=str(manifest.run_id),
        qualified_runtime_commit=decision.qualified_runtime_commit,
        dataset_id=manifest.dataset_id,
        dataset_version=manifest.dataset_version,
        dataset_hash=manifest.dataset_hash,
        provider=provider.provider,
        model=provider.model,
        provider_sdk=provider.provider_sdk,
        provider_sdk_version=provider.provider_sdk_version,
        prompt_id=provider.prompt_id,
        prompt_version=provider.prompt_version,
        prompt_hash=provider.prompt_hash,
        interpretation_schema_version=provider.interpretation_schema_version,
        scorer_id=manifest.scorer_id,
        scorer_version=manifest.scorer_version,
        policy_id=manifest.policy_id,
        policy_version=manifest.policy_version,
        policy_hash=manifest.policy_hash,
        settings_fingerprint=manifest.settings_fingerprint,
        endpoint_fingerprint=provider.endpoint_fingerprint,
        repeat_count=2,
        evaluation_count=240,
        absolute_gate_passed=True,
        critical_gate_passed=True,
        stability_gate_passed=True,
        relative_gate_evaluated=False,
        baseline_eligible=True,
        artifact_hashes=artifact_hashes,
    )


def _source_hashes(decision: QualificationDecision) -> tuple[SourceArtifactHash, ...]:
    return (
        SourceArtifactHash(
            relative_path="artifact-index.json",
            sha256=decision.source_hashes.artifact_index_sha256,
        ),
        SourceArtifactHash(
            relative_path="summary.json",
            sha256=decision.source_hashes.summary_sha256,
        ),
        SourceArtifactHash(
            relative_path="gate-result.json",
            sha256=decision.source_hashes.gate_result_sha256,
        ),
    )


def _safe_outcome(result: EvaluationCaseResult) -> SafeCaseOutcome:
    interpretation = result.interpretation
    fingerprint = (
        sha256_value(interpretation.model_dump(mode="json")) if interpretation is not None else None
    )
    return SafeCaseOutcome(
        case_id=result.case_id,
        repeat_index=result.repeat_index,
        suite=result.suite,
        status=result.status,
        case_passed=result.case_passed,
        interpretation_fingerprint=fingerprint,
        intent=interpretation.utterance_intent if interpretation else None,
        clarification_needed=(
            bool(interpretation.model_suggested_missing_fields)
            if interpretation is not None
            else None
        ),
        missing_fields=(
            tuple(item.value for item in interpretation.model_suggested_missing_fields)
            if interpretation is not None
            else ()
        ),
        safety_signals=(
            tuple(item.value for item in interpretation.safety_flags)
            if interpretation is not None
            else ()
        ),
        critical_failure_codes=result.critical_failure_codes,
        provider_error_code=result.provider_error_code,
        attempt_count=result.attempt_count,
        latency_ms=result.latency_ms,
        usage=result.usage,
    )


def _checksums(directory: Path, names: frozenset[str] | set[str]) -> tuple[EvidenceChecksum, ...]:
    return tuple(
        EvidenceChecksum(
            relative_path=name,
            sha256=sha256_text((directory / name).read_text(encoding="utf-8")),
            size_bytes=(directory / name).stat().st_size,
        )
        for name in sorted(names)
    )


def _require_within(target: Path, root: Path) -> None:
    root_resolved = root.resolve()
    target_resolved = target.resolve()
    if not target_resolved.is_relative_to(root_resolved):
        raise ArtifactError("evidence output path escapes the allowed root")
    if target.name in {"", ".", ".."}:
        raise ArtifactError("evidence output path is invalid")


def _assert_identical_directories(expected: Path, actual: Path) -> None:
    expected_files = {path.name for path in expected.iterdir() if path.is_file()}
    actual_files = {path.name for path in actual.iterdir() if path.is_file()}
    if expected_files != actual_files:
        raise ArtifactError("baseline exists with different files")
    if any(
        (expected / name).read_bytes() != (actual / name).read_bytes() for name in expected_files
    ):
        raise ArtifactError("baseline exists with different content")

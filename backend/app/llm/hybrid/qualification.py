"""Formal qualification and non-activated publication for hybrid interpretation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field

from app.llm.evaluation.artifacts import atomic_write_text
from app.llm.evaluation.errors import ArtifactError
from app.llm.evaluation.hashing import sha256_text, sha256_value
from app.llm.evaluation.models import EvaluationMetrics, EvaluationModel, Sha256
from app.llm.hybrid.evaluation import (
    HybridEvaluationReport,
    HybridStabilityReport,
    compare_hybrid_repeats,
)
from app.llm.hybrid.models import HybridInterpretationMetadata

HYBRID_SCORER_ID: Literal["resident_hybrid_interpretation_scorer"] = (
    "resident_hybrid_interpretation_scorer"
)
HYBRID_SCORER_VERSION: Literal["1.1.0"] = "1.1.0"
HYBRID_GATE_ID: Literal["resident_hybrid_interpretation_gate"] = (
    "resident_hybrid_interpretation_gate"
)
HYBRID_GATE_VERSION: Literal["1.0.0"] = "1.0.0"
HYBRID_GATE_POLICY = {
    "completion_rate": 1.0,
    "parse_pass_rate": 0.99,
    "schema_pass_rate": 0.99,
    "invariant_pass_rate": 0.99,
    "intent_accuracy": 0.95,
    "clarification_accuracy": 0.95,
    "missing_fields_f1": 0.90,
    "safety_signal_recall": 0.98,
    "critical_safety_recall": 1.0,
    "request_human_boundary_accuracy": 1.0,
    "intent_consistency_rate": 0.98,
    "clarification_consistency_rate": 0.98,
    "missing_fields_consistency_rate": 0.95,
    "safety_consistency_rate": 0.98,
    "critical_safety_consistency_rate": 1.0,
    "request_human_consistency_rate": 1.0,
}
HYBRID_GATE_HASH = sha256_value(HYBRID_GATE_POLICY)


class HybridQualificationStatus(StrEnum):
    QUALIFIED = "QUALIFIED"
    NOT_QUALIFIED = "NOT_QUALIFIED"


class HybridActivationStatus(StrEnum):
    NOT_ACTIVATED = "NOT_ACTIVATED"


class HybridReleaseCandidateStatus(StrEnum):
    QUALIFIED_CANDIDATE = "QUALIFIED_CANDIDATE"


class HybridArtifactHash(EvaluationModel):
    label: str = Field(pattern=r"^[a-z][a-z0-9-]{1,60}$")
    sha256: Sha256


class HybridCorpusEvidence(EvaluationModel):
    dataset_id: str
    dataset_version: str
    dataset_hash: Sha256
    first_run_id: str
    second_run_id: str
    first_metrics: EvaluationMetrics
    second_metrics: EvaluationMetrics
    stability: HybridStabilityReport


class HybridQualificationEvidence(EvaluationModel):
    qualification_schema_version: Literal["hybrid-qualification-v1"] = "hybrid-qualification-v1"
    status: HybridQualificationStatus
    runtime_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    architecture: HybridInterpretationMetadata
    provider: Literal["deepseek"]
    model: Literal["deepseek-v4-flash", "deepseek-v4-pro"]
    scorer_id: Literal["resident_hybrid_interpretation_scorer"] = HYBRID_SCORER_ID
    scorer_version: Literal["1.1.0"] = HYBRID_SCORER_VERSION
    gate_id: Literal["resident_hybrid_interpretation_gate"] = HYBRID_GATE_ID
    gate_version: Literal["1.0.0"] = HYBRID_GATE_VERSION
    gate_hash: Sha256 = HYBRID_GATE_HASH
    regression: HybridCorpusEvidence
    challenge: HybridCorpusEvidence
    all_absolute_gates_passed: bool
    all_stability_gates_passed: bool
    artifact_integrity_passed: bool
    runtime_identity_passed: bool
    challenge_consumed: bool
    artifact_hashes: tuple[HybridArtifactHash, ...] = Field(min_length=4, max_length=4)


class HybridUsageEvidence(EvaluationModel):
    evaluation_calls: int = Field(ge=0)
    upstream_attempts: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    maximum_p95_latency_ms: float = Field(ge=0)


class HybridBaselineManifest(EvaluationModel):
    baseline_schema_version: Literal["hybrid-baseline-v1"] = "hybrid-baseline-v1"
    baseline_id: Literal["resident-hybrid-interpretation-deepseek-v4-flash-baseline-1"]
    baseline_version: Literal["1.0.0"] = "1.0.0"
    status: Literal["QUALIFIED"] = "QUALIFIED"
    created_at_utc: datetime
    runtime_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    architecture: HybridInterpretationMetadata
    provider: Literal["deepseek"]
    model: Literal["deepseek-v4-flash"]
    sdk_version: str
    endpoint_fingerprint: Sha256
    scorer_id: Literal["resident_hybrid_interpretation_scorer"] = HYBRID_SCORER_ID
    scorer_version: Literal["1.1.0"] = HYBRID_SCORER_VERSION
    policy_id: Literal["resident_hybrid_interpretation_gate"] = HYBRID_GATE_ID
    policy_version: Literal["1.0.0"] = HYBRID_GATE_VERSION
    policy_hash: Sha256 = HYBRID_GATE_HASH
    regression: HybridCorpusEvidence
    challenge: HybridCorpusEvidence
    usage: HybridUsageEvidence
    artifact_hashes: tuple[HybridArtifactHash, ...] = Field(min_length=4, max_length=4)
    known_limitations: tuple[str, ...] = Field(min_length=5)
    activation: HybridActivationStatus = HybridActivationStatus.NOT_ACTIVATED
    default_provider: Literal["scripted"] = "scripted"


class HybridBaselineEnvelope(EvaluationModel):
    manifest: HybridBaselineManifest
    manifest_sha256: Sha256


class HybridReleaseCandidate(EvaluationModel):
    release_candidate_schema_version: Literal["hybrid-release-candidate-v1"] = (
        "hybrid-release-candidate-v1"
    )
    release_candidate_id: Literal["deepseek-v4-flash-hybrid-resident-interpretation-rc1"]
    release_candidate_version: Literal["1.0.0"] = "1.0.0"
    status: HybridReleaseCandidateStatus = HybridReleaseCandidateStatus.QUALIFIED_CANDIDATE
    baseline_id: Literal["resident-hybrid-interpretation-deepseek-v4-flash-baseline-1"]
    baseline_version: Literal["1.0.0"]
    baseline_manifest_sha256: Sha256
    runtime_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    activation: HybridActivationStatus = HybridActivationStatus.NOT_ACTIVATED
    default_provider: Literal["scripted"] = "scripted"


def qualify_formal_reports(
    report_paths: tuple[Path, Path, Path, Path],
) -> HybridQualificationEvidence:
    reports = tuple(_read_report(path) for path in report_paths)
    regression = (reports[0], reports[1])
    challenge = (reports[2], reports[3])
    _verify_report_pair(regression, expected_stage="FORMAL_REGRESSION")
    _verify_report_pair(challenge, expected_stage="FORMAL_CHALLENGE")
    identities = {
        (
            report.runtime_commit,
            report.architecture.model_dump_json(),
            report.provider,
            report.model,
        )
        for report in reports
    }
    if len(identities) != 1:
        raise ArtifactError("formal report runtime identities do not match")
    runtime_commit = reports[0].runtime_commit
    if runtime_commit is None:
        raise ArtifactError("formal report is missing runtime commit")
    artifacts = tuple(
        HybridArtifactHash(label=label, sha256=sha256_text(path.read_text(encoding="utf-8")))
        for label, path in zip(
            ("regression-r1", "regression-r2", "challenge-r1", "challenge-r2"),
            report_paths,
            strict=True,
        )
    )
    regression_evidence = _corpus_evidence(regression)
    challenge_evidence = _corpus_evidence(challenge)
    absolute = all(report.gate.passed for report in reports)
    stability = regression_evidence.stability.passed and challenge_evidence.stability.passed
    challenge_consumed = all(report.challenge_consumed for report in challenge)
    identity = all(report.source_clean for report in reports)
    status = (
        HybridQualificationStatus.QUALIFIED
        if absolute and stability and challenge_consumed and identity
        else HybridQualificationStatus.NOT_QUALIFIED
    )
    return HybridQualificationEvidence(
        status=status,
        runtime_commit=runtime_commit,
        architecture=reports[0].architecture,
        provider=reports[0].provider,
        model=reports[0].model,
        regression=regression_evidence,
        challenge=challenge_evidence,
        all_absolute_gates_passed=absolute,
        all_stability_gates_passed=stability,
        artifact_integrity_passed=True,
        runtime_identity_passed=identity,
        challenge_consumed=challenge_consumed,
        artifact_hashes=artifacts,
    )


def publish_qualified_candidate(
    qualification: HybridQualificationEvidence,
    *,
    output_directory: Path,
    allowed_root: Path,
    endpoint_fingerprint: str,
    sdk_version: str,
) -> tuple[HybridBaselineEnvelope, HybridReleaseCandidate]:
    _require_within(output_directory, allowed_root)
    if qualification.status is not HybridQualificationStatus.QUALIFIED:
        raise ArtifactError("formal hybrid qualification did not pass")
    if qualification.model != "deepseek-v4-flash":
        raise ArtifactError("baseline identity is frozen for DeepSeek V4 Flash")
    reports = (qualification.regression, qualification.challenge)
    usage = HybridUsageEvidence(
        evaluation_calls=sum(
            report.first_metrics.total_cases + report.second_metrics.total_cases
            for report in reports
        ),
        upstream_attempts=sum(
            report.first_metrics.total_cases + report.second_metrics.total_cases
            for report in reports
        ),
        input_tokens=sum(
            report.first_metrics.total_input_tokens + report.second_metrics.total_input_tokens
            for report in reports
        ),
        output_tokens=sum(
            report.first_metrics.total_output_tokens + report.second_metrics.total_output_tokens
            for report in reports
        ),
        total_tokens=sum(
            report.first_metrics.total_tokens + report.second_metrics.total_tokens
            for report in reports
        ),
        maximum_p95_latency_ms=max(
            metric.p95_latency_ms or 0
            for report in reports
            for metric in (report.first_metrics, report.second_metrics)
        ),
    )
    baseline = HybridBaselineManifest(
        baseline_id="resident-hybrid-interpretation-deepseek-v4-flash-baseline-1",
        created_at_utc=datetime.now(UTC),
        runtime_commit=qualification.runtime_commit,
        architecture=qualification.architecture,
        provider=qualification.provider,
        model=qualification.model,
        sdk_version=sdk_version,
        endpoint_fingerprint=endpoint_fingerprint,
        regression=qualification.regression,
        challenge=qualification.challenge,
        usage=usage,
        artifact_hashes=qualification.artifact_hashes,
        known_limitations=(
            "Both corpora contain synthetic engineering examples.",
            "No real resident traffic was evaluated.",
            "No production latency or cost SLA was established.",
            "The candidate is not activated and serves no traffic.",
            "The default product provider remains scripted.",
        ),
    )
    manifest_json = baseline.model_dump_json(indent=2)
    envelope = HybridBaselineEnvelope(
        manifest=baseline,
        manifest_sha256=sha256_text(baseline.model_dump_json()),
    )
    candidate = HybridReleaseCandidate(
        release_candidate_id="deepseek-v4-flash-hybrid-resident-interpretation-rc1",
        baseline_id=baseline.baseline_id,
        baseline_version=baseline.baseline_version,
        baseline_manifest_sha256=envelope.manifest_sha256,
        runtime_commit=baseline.runtime_commit,
    )
    output_directory.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        output_directory / "qualification.json",
        qualification.model_dump_json(indent=2) + "\n",
    )
    atomic_write_text(
        output_directory / "baseline.json",
        envelope.model_dump_json(indent=2) + "\n",
    )
    atomic_write_text(
        output_directory / "release-candidate.json",
        candidate.model_dump_json(indent=2) + "\n",
    )
    verified = verify_published_candidate(output_directory)
    if verified[0].manifest.model_dump_json() != baseline.model_dump_json():
        raise ArtifactError("reloaded hybrid baseline identity mismatch")
    if sha256_text(manifest_json) == "":
        raise ArtifactError("unreachable empty baseline hash")
    return verified


def verify_published_candidate(
    directory: Path,
) -> tuple[HybridBaselineEnvelope, HybridReleaseCandidate]:
    expected = {"qualification.json", "baseline.json", "release-candidate.json"}
    actual = {path.name for path in directory.iterdir() if path.is_file()}
    if actual != expected:
        raise ArtifactError("hybrid baseline package file set mismatch")
    qualification = HybridQualificationEvidence.model_validate_json(
        (directory / "qualification.json").read_text(encoding="utf-8")
    )
    envelope = HybridBaselineEnvelope.model_validate_json(
        (directory / "baseline.json").read_text(encoding="utf-8")
    )
    candidate = HybridReleaseCandidate.model_validate_json(
        (directory / "release-candidate.json").read_text(encoding="utf-8")
    )
    if qualification.status is not HybridQualificationStatus.QUALIFIED:
        raise ArtifactError("published hybrid qualification is not qualified")
    expected_hash = sha256_text(envelope.manifest.model_dump_json())
    if envelope.manifest_sha256 != expected_hash:
        raise ArtifactError("hybrid baseline checksum mismatch")
    if candidate.baseline_manifest_sha256 != envelope.manifest_sha256:
        raise ArtifactError("hybrid candidate baseline checksum mismatch")
    if candidate.runtime_commit != envelope.manifest.runtime_commit:
        raise ArtifactError("hybrid candidate runtime identity mismatch")
    content = "".join(
        (directory / name).read_text(encoding="utf-8") for name in sorted(expected)
    ).casefold()
    forbidden = (
        '"authorization":',
        '"password":',
        '"database_url":',
        '"raw_response":',
        "bearer ",
    )
    if any(value in content for value in forbidden):
        raise ArtifactError("hybrid publication contains forbidden secret material")
    return envelope, candidate


def _read_report(path: Path) -> HybridEvaluationReport:
    try:
        return HybridEvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactError("formal hybrid report is missing or invalid") from exc


def _verify_report_pair(
    reports: tuple[HybridEvaluationReport, HybridEvaluationReport],
    *,
    expected_stage: str,
) -> None:
    if any(report.stage != expected_stage for report in reports):
        raise ArtifactError("formal hybrid report stage mismatch")
    if reports[0].run_id == reports[1].run_id:
        raise ArtifactError("formal hybrid repeats must be independent runs")
    if reports[0].dataset_hash != reports[1].dataset_hash:
        raise ArtifactError("formal hybrid repeat dataset identity mismatch")
    if any(report.evaluation_call_count != len(report.cases) for report in reports):
        raise ArtifactError("formal hybrid report is incomplete")


def _corpus_evidence(
    reports: tuple[HybridEvaluationReport, HybridEvaluationReport],
) -> HybridCorpusEvidence:
    first, second = reports
    return HybridCorpusEvidence(
        dataset_id=first.dataset_id,
        dataset_version=first.dataset_version,
        dataset_hash=first.dataset_hash,
        first_run_id=str(first.run_id),
        second_run_id=str(second.run_id),
        first_metrics=first.metrics,
        second_metrics=second.metrics,
        stability=compare_hybrid_repeats(first, second),
    )


def _require_within(path: Path, allowed_root: Path) -> None:
    target = path.resolve()
    root = allowed_root.resolve()
    if target == root or root not in target.parents:
        raise ArtifactError("hybrid publication path is outside the allowed root")

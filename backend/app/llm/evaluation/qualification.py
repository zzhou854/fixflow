"""Offline-only qualification evidence for a completed live evaluation run."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from pydantic import ConfigDict, Field

from app.llm.evaluation.artifacts import ARTIFACT_FILES
from app.llm.evaluation.errors import ArtifactError
from app.llm.evaluation.hashing import sha256_text
from app.llm.evaluation.models import (
    CriticalFailureCode,
    EvaluationArtifactIndex,
    EvaluationCaseResult,
    EvaluationModel,
    EvaluationReport,
    EvaluationRunStatus,
    GitCommit,
    Sha256,
)

QUALIFIED_RUNTIME_COMMIT = "28ef2d6383ed51280cf34f60fa3fb38f1fe6d287"
DATASET_HASH = "a0dfb91aed5653eafcb5649beee1f9106ac4d6b332df50923c68d5f02e979296"
PROMPT_HASH = "8c161de155a0b3bb42b00ba516e90fa5526912945126c88538c452171e1f94d9"
POLICY_HASH = "7391042124ab2aec9eccd796b4493a4184e0722748f55528c5cb22e7aa1b134e"
ENDPOINT_FINGERPRINT = "99d56f9dd588b28192c4f59b08ba0c1bc3cbb05930d53d5a3cb609e2db2499af"
EXPECTED_EVALUATIONS = 240


class QualificationStatus(StrEnum):
    QUALIFIED = "QUALIFIED"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    INCOMPLETE = "INCOMPLETE"


class RelativeGateStatus(StrEnum):
    NOT_APPLICABLE_INITIAL_BASELINE = "NOT_APPLICABLE_INITIAL_BASELINE"


class QualificationFailure(EvaluationModel):
    code: str = Field(min_length=1, max_length=100)
    detail: str = Field(min_length=1, max_length=300)


class CriticalFailureEvidence(EvaluationModel):
    case_id: str = Field(min_length=1, max_length=80)
    repeat_index: int = Field(ge=0, le=4)
    code: CriticalFailureCode


class QualificationStabilityResult(EvaluationModel):
    stability_schema_version: str = "qualification-stability-v1"
    intent_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    clarification_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    missing_fields_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    safety_signal_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    critical_safety_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    request_human_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    exact_output_consistency_rate: float | None = Field(default=None, ge=0, le=1)
    gate_passed: bool
    failed_rules: tuple[str, ...] = ()


class QualificationProviderEvidence(EvaluationModel):
    total_evaluation_calls: int = Field(ge=0)
    total_upstream_attempts: int = Field(ge=0)
    cases_with_retry: int = Field(ge=0)
    maximum_latency_ms: int | None = Field(default=None, ge=0)


class QualificationSourceHashes(EvaluationModel):
    artifact_index_sha256: Sha256
    summary_sha256: Sha256
    gate_result_sha256: Sha256


class QualificationDecision(EvaluationModel):
    qualification_schema_version: str = "model-qualification-v1"
    status: QualificationStatus
    qualified_runtime_commit: GitCommit
    artifact_integrity_passed: bool
    identity_passed: bool
    absolute_gate_passed: bool
    critical_gate_passed: bool
    stability: QualificationStabilityResult
    relative_gate_status: RelativeGateStatus
    baseline_eligible: bool
    baseline_created: bool = False
    release_candidate_created: bool = False
    failures: tuple[QualificationFailure, ...] = ()
    failed_case_ids: tuple[str, ...] = ()
    critical_failures: tuple[CriticalFailureEvidence, ...] = ()
    provider_evidence: QualificationProviderEvidence
    source_hashes: QualificationSourceHashes


class QualificationRun(EvaluationModel):
    """Validated in-memory view; raw results are never serialized by this model."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    report: EvaluationReport
    results: tuple[EvaluationCaseResult, ...]
    decision: QualificationDecision


def load_and_qualify(
    run_directory: Path,
    *,
    network_authorized: bool,
    cost_acknowledged: bool,
) -> QualificationRun:
    """Verify one immutable run and derive its qualification deterministically."""
    report, results, hashes = _load_verified_artifacts(run_directory)
    decision = qualify(
        report,
        results,
        source_hashes=hashes,
        network_authorized=network_authorized,
        cost_acknowledged=cost_acknowledged,
    )
    return QualificationRun(report=report, results=results, decision=decision)


def qualify(
    report: EvaluationReport,
    results: tuple[EvaluationCaseResult, ...],
    *,
    source_hashes: QualificationSourceHashes,
    network_authorized: bool,
    cost_acknowledged: bool,
) -> QualificationDecision:
    manifest = report.manifest
    gate = report.gate_result
    stability = calculate_stability(results)
    failures = list(_identity_failures(report))
    if not network_authorized:
        failures.append(QualificationFailure(code="NETWORK_NOT_AUTHORIZED", detail="missing flag"))
    if not cost_acknowledged:
        failures.append(QualificationFailure(code="COST_NOT_ACKNOWLEDGED", detail="missing flag"))
    if len(results) != EXPECTED_EVALUATIONS:
        failures.append(
            QualificationFailure(
                code="EVALUATION_COUNT_MISMATCH",
                detail=f"expected {EXPECTED_EVALUATIONS}, got {len(results)}",
            )
        )
    absolute_passed = bool(gate and gate.absolute_gate_passed)
    critical_passed = bool(gate and not gate.critical_failures)
    if not absolute_passed:
        failures.append(QualificationFailure(code="ABSOLUTE_GATE_FAILED", detail="gate failed"))
    if not critical_passed:
        failures.append(QualificationFailure(code="CRITICAL_GATE_FAILED", detail="gate failed"))
    if not stability.gate_passed:
        failures.append(QualificationFailure(code="STABILITY_GATE_FAILED", detail="gate failed"))

    complete = (
        manifest.run_status is EvaluationRunStatus.COMPLETED
        and len(results) == EXPECTED_EVALUATIONS
        and report.metrics.completed_cases == EXPECTED_EVALUATIONS
    )
    identity_passed = not any(
        item.code.endswith("_MISMATCH") or item.code in {"FAKE_PROVIDER", "DIRTY_RUN"}
        for item in failures
    )
    all_passed = (
        complete
        and identity_passed
        and network_authorized
        and cost_acknowledged
        and absolute_passed
        and critical_passed
        and stability.gate_passed
    )
    if not complete:
        status = QualificationStatus.INCOMPLETE
    elif all_passed:
        status = QualificationStatus.QUALIFIED
    else:
        status = QualificationStatus.NOT_QUALIFIED

    critical_rows = tuple(
        CriticalFailureEvidence(
            case_id=result.case_id,
            repeat_index=result.repeat_index,
            code=code,
        )
        for result in results
        for code in result.critical_failure_codes
    )
    provider_evidence = QualificationProviderEvidence(
        total_evaluation_calls=len(results),
        total_upstream_attempts=sum(item.attempt_count for item in results),
        cases_with_retry=sum(item.attempt_count > 1 for item in results),
        maximum_latency_ms=max((item.latency_ms for item in results), default=None),
    )
    return QualificationDecision(
        status=status,
        qualified_runtime_commit=QUALIFIED_RUNTIME_COMMIT,
        artifact_integrity_passed=True,
        identity_passed=identity_passed,
        absolute_gate_passed=absolute_passed,
        critical_gate_passed=critical_passed,
        stability=stability,
        relative_gate_status=RelativeGateStatus.NOT_APPLICABLE_INITIAL_BASELINE,
        baseline_eligible=all_passed,
        failures=tuple(failures),
        failed_case_ids=tuple(sorted({item.case_id for item in results if not item.case_passed})),
        critical_failures=critical_rows,
        provider_evidence=provider_evidence,
        source_hashes=source_hashes,
    )


def calculate_stability(
    results: tuple[EvaluationCaseResult, ...],
) -> QualificationStabilityResult:
    grouped: dict[str, list[EvaluationCaseResult]] = defaultdict(list)
    for result in results:
        grouped[result.case_id].append(result)
    groups = tuple(
        tuple(sorted(items, key=lambda item: item.repeat_index)) for items in grouped.values()
    )
    valid_pairs = tuple(
        group
        for group in groups
        if len(group) == 2 and tuple(item.repeat_index for item in group) == (0, 1)
    )
    pairs_are_complete = len(valid_pairs) == len(groups)
    critical = tuple(group for group in valid_pairs if group[0].severity.value == "CRITICAL")
    human = tuple(group for group in valid_pairs if group[0].suite.value == "REQUEST_HUMAN")

    intent = _consistency(valid_pairs, lambda item: _interpretation(item, "intent"))
    clarification = _consistency(valid_pairs, lambda item: _interpretation(item, "missing"))
    missing = clarification
    safety = _consistency(valid_pairs, lambda item: _interpretation(item, "safety"))
    critical_safety = _consistency(critical, lambda item: _interpretation(item, "safety"))
    request_human = _consistency(human, lambda item: _interpretation(item, "human"))
    exact = _consistency(valid_pairs, lambda item: _interpretation(item, "exact"))
    rules = (
        ("intent_consistency_rate", intent, 0.98),
        ("clarification_consistency_rate", clarification, 0.98),
        ("missing_fields_consistency_rate", missing, 0.95),
        ("critical_safety_consistency_rate", critical_safety, 1.0),
        ("request_human_consistency_rate", request_human, 1.0),
    )
    failed = tuple(
        name for name, actual, threshold in rules if actual is None or actual < threshold
    )
    if not pairs_are_complete:
        failed = ("repeat_pair_integrity", *failed)
    return QualificationStabilityResult(
        intent_consistency_rate=intent,
        clarification_consistency_rate=clarification,
        missing_fields_consistency_rate=missing,
        safety_signal_consistency_rate=safety,
        critical_safety_consistency_rate=critical_safety,
        request_human_consistency_rate=request_human,
        exact_output_consistency_rate=exact,
        gate_passed=not failed,
        failed_rules=failed,
    )


def _load_verified_artifacts(
    run_directory: Path,
) -> tuple[EvaluationReport, tuple[EvaluationCaseResult, ...], QualificationSourceHashes]:
    if not run_directory.is_dir():
        raise ArtifactError("qualification run directory is missing")
    index_path = run_directory / "artifact-index.json"
    try:
        index_text = index_path.read_text(encoding="utf-8")
        index = EvaluationArtifactIndex.model_validate_json(index_text)
    except (OSError, ValueError) as exc:
        raise ArtifactError("artifact index is missing or damaged") from exc
    if set(index.files) != set(ARTIFACT_FILES):
        raise ArtifactError("artifact index file set is incomplete")
    expected_names = set(ARTIFACT_FILES) - {"artifact-index.json"}
    if set(index.file_hashes) != expected_names:
        raise ArtifactError("artifact index hashes are incomplete")
    for name, expected_hash in index.file_hashes.items():
        if Path(name).name != name:
            raise ArtifactError("artifact index contains an unsafe path")
        path = run_directory / name
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ArtifactError("indexed artifact is missing") from exc
        if sha256_text(content) != expected_hash:
            raise ArtifactError("indexed artifact hash mismatch")
    try:
        report = EvaluationReport.model_validate_json(
            (run_directory / "summary.json").read_text(encoding="utf-8")
        )
        results = tuple(
            EvaluationCaseResult.model_validate_json(line)
            for line in (run_directory / "case-results.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        )
    except (OSError, ValueError) as exc:
        raise ArtifactError("qualification artifacts are invalid") from exc
    if index.run_id != report.manifest.run_id:
        raise ArtifactError("artifact index run identity mismatch")
    return (
        report,
        results,
        QualificationSourceHashes(
            artifact_index_sha256=sha256_text(index_text),
            summary_sha256=sha256_text(
                (run_directory / "summary.json").read_text(encoding="utf-8")
            ),
            gate_result_sha256=sha256_text(
                (run_directory / "gate-result.json").read_text(encoding="utf-8")
            ),
        ),
    )


def _identity_failures(report: EvaluationReport) -> tuple[QualificationFailure, ...]:
    manifest = report.manifest
    provider = manifest.provider_configuration
    checks = (
        ("RUNTIME_COMMIT_MISMATCH", manifest.code_commit, QUALIFIED_RUNTIME_COMMIT),
        ("DATASET_ID_MISMATCH", manifest.dataset_id, "resident_interpretation"),
        ("DATASET_VERSION_MISMATCH", manifest.dataset_version, "1.0.0"),
        ("DATASET_HASH_MISMATCH", manifest.dataset_hash, DATASET_HASH),
        ("PROMPT_ID_MISMATCH", provider.prompt_id, "resident_interpretation"),
        ("PROMPT_VERSION_MISMATCH", provider.prompt_version, "1.0.0"),
        ("PROMPT_HASH_MISMATCH", provider.prompt_hash, PROMPT_HASH),
        ("SCHEMA_MISMATCH", provider.interpretation_schema_version, "interpretation-result-v1"),
        ("SCORER_ID_MISMATCH", manifest.scorer_id, "resident_interpretation_scorer"),
        ("SCORER_VERSION_MISMATCH", manifest.scorer_version, "1.0.0"),
        ("POLICY_ID_MISMATCH", manifest.policy_id, "resident_interpretation_gate"),
        ("POLICY_VERSION_MISMATCH", manifest.policy_version, "1.0.0"),
        ("POLICY_HASH_MISMATCH", manifest.policy_hash, POLICY_HASH),
        ("PROVIDER_MISMATCH", provider.provider, "zai"),
        ("MODEL_MISMATCH", provider.model, "glm-5.1"),
        ("SDK_MISMATCH", provider.provider_sdk, "zai-sdk"),
        ("SDK_VERSION_MISMATCH", provider.provider_sdk_version, "0.2.3"),
        ("ENDPOINT_MISMATCH", provider.endpoint_fingerprint, ENDPOINT_FINGERPRINT),
        ("REPEAT_COUNT_MISMATCH", manifest.repeat_count, 2),
        ("CONCURRENCY_MISMATCH", manifest.concurrency, 1),
        ("LIVE_NETWORK_MISMATCH", provider.live_network, True),
    )
    failures = [
        QualificationFailure(code=code, detail="identity did not match the frozen value")
        for code, actual, expected in checks
        if actual != expected
    ]
    if manifest.git_dirty is not False:
        failures.append(QualificationFailure(code="DIRTY_RUN", detail="run was not clean"))
    if provider.provider.endswith("fake"):
        failures.append(QualificationFailure(code="FAKE_PROVIDER", detail="provider was fake"))
    return tuple(failures)


def _consistency(
    groups: tuple[tuple[EvaluationCaseResult, ...], ...],
    projection: Callable[[EvaluationCaseResult], object],
) -> float | None:
    repeated = tuple(group for group in groups if len(group) == 2)
    if not repeated:
        return None
    return sum(projection(group[0]) == projection(group[1]) for group in repeated) / len(repeated)


def _interpretation(result: EvaluationCaseResult, field: str) -> object:
    value = result.interpretation
    if value is None:
        return None
    if field == "intent":
        return value.utterance_intent
    if field == "missing":
        return value.model_suggested_missing_fields
    if field == "safety":
        return value.safety_flags
    if field == "human":
        return value.requested_human
    return value.model_dump_json()

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from app.agent.enums import AgentIntent, IssueField, SafetyFlag
from app.agent.models import InterpretMessageOutput
from app.llm.evaluation.artifacts import EvaluationArtifactStore
from app.llm.evaluation.errors import ArtifactError
from app.llm.evaluation.models import (
    CriticalFailureCode,
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationDataset,
    EvaluationGateResult,
    EvaluationProviderConfiguration,
    EvaluationReport,
    EvaluationRunManifest,
    EvaluationRunStatus,
    EvaluationSeverity,
    EvaluationSuite,
    EvaluationUsage,
)
from app.llm.evaluation.qualification import (
    DATASET_HASH,
    ENDPOINT_FINGERPRINT,
    POLICY_HASH,
    PROMPT_HASH,
    QUALIFIED_RUNTIME_COMMIT,
    QualificationSourceHashes,
    QualificationStatus,
    calculate_stability,
    load_and_qualify,
    qualify,
)
from app.llm.evaluation.scorer import _critical_failures
from tests.llm.evaluation.helpers import passing_metrics

HASHES = QualificationSourceHashes(
    artifact_index_sha256="1" * 64,
    summary_sha256="2" * 64,
    gate_result_sha256="3" * 64,
)


def _result(
    case_index: int,
    repeat_index: int,
    *,
    suite: EvaluationSuite = EvaluationSuite.CREATE_TICKET,
    severity: EvaluationSeverity = EvaluationSeverity.STANDARD,
    intent: AgentIntent = AgentIntent.NEW_REPAIR,
    missing: tuple[IssueField, ...] = (),
    safety: tuple[SafetyFlag, ...] = (),
    requested_human: bool = False,
    case_passed: bool = True,
    critical: tuple[CriticalFailureCode, ...] = (),
    attempts: int = 1,
) -> EvaluationCaseResult:
    now = datetime(2026, 7, 23, tzinfo=UTC)
    return EvaluationCaseResult(
        case_id=f"qualification-{case_index:03d}",
        suite=suite,
        severity=severity,
        repeat_index=repeat_index,
        status=(
            EvaluationCaseStatus.PASSED if case_passed else EvaluationCaseStatus.FAILED_EXPECTATION
        ),
        provider="zai",
        model="glm-5.1",
        prompt_id="resident_interpretation",
        prompt_version="1.0.0",
        prompt_hash=PROMPT_HASH,
        schema_version="interpretation-result-v1",
        input_hash=f"{case_index + 1:064x}",
        started_at=now,
        completed_at=now,
        latency_ms=100 + case_index,
        attempt_count=attempts,
        interpretation=InterpretMessageOutput(
            utterance_intent=intent,
            model_suggested_missing_fields=missing,
            safety_flags=safety,
            requested_human=requested_human,
        ),
        case_passed=case_passed,
        critical_failure_codes=critical,
        usage=EvaluationUsage(input_tokens=10, output_tokens=5, total_tokens=15),
    )


def _results() -> tuple[EvaluationCaseResult, ...]:
    items: list[EvaluationCaseResult] = []
    for index in range(120):
        suite = EvaluationSuite.REQUEST_HUMAN if index < 10 else EvaluationSuite.CREATE_TICKET
        severity = EvaluationSeverity.CRITICAL if 10 <= index < 26 else EvaluationSeverity.STANDARD
        for repeat in range(2):
            items.append(
                _result(
                    index,
                    repeat,
                    suite=suite,
                    severity=severity,
                    safety=(SafetyFlag.IMMEDIATE_DANGER,)
                    if severity is EvaluationSeverity.CRITICAL
                    else (),
                    requested_human=suite is EvaluationSuite.REQUEST_HUMAN,
                )
            )
    return tuple(items)


def _report(
    *,
    absolute: bool = True,
    critical: tuple[CriticalFailureCode, ...] = (),
) -> EvaluationReport:
    now = datetime(2026, 7, 23, tzinfo=UTC)
    provider = EvaluationProviderConfiguration(
        provider="zai",
        model="glm-5.1",
        provider_sdk="zai-sdk",
        provider_sdk_version="0.2.3",
        prompt_id="resident_interpretation",
        prompt_version="1.0.0",
        prompt_hash=PROMPT_HASH,
        interpretation_schema_version="interpretation-result-v1",
        thinking_mode="disabled",
        temperature=0.1,
        top_p=0.8,
        max_tokens=1600,
        request_timeout_seconds=20,
        total_timeout_seconds=30,
        max_attempts=3,
        endpoint_fingerprint=ENDPOINT_FINGERPRINT,
        live_network=True,
    )
    manifest = EvaluationRunManifest(
        run_id=UUID(int=15),
        run_schema_version="evaluation-run-v1",
        run_status=EvaluationRunStatus.COMPLETED,
        dataset_id="resident_interpretation",
        dataset_version="1.0.0",
        dataset_hash=DATASET_HASH,
        policy_id="resident_interpretation_gate",
        policy_version="1.0.0",
        policy_hash=POLICY_HASH,
        scorer_id="resident_interpretation_scorer",
        scorer_version="1.0.0",
        provider_configuration=provider,
        concurrency=1,
        repeat_count=2,
        code_commit=QUALIFIED_RUNTIME_COMMIT,
        git_dirty=False,
        settings_fingerprint="4" * 64,
        created_at_utc=now,
        completed_at_utc=now,
        case_count=120,
    )
    gate = EvaluationGateResult(
        gate_id=UUID(int=16),
        gate_version="evaluation-gate-result-v1",
        run_id=manifest.run_id,
        scorer_id="resident_interpretation_scorer",
        scorer_version="1.0.0",
        policy_id="resident_interpretation_gate",
        policy_version="1.0.0",
        policy_hash=POLICY_HASH,
        absolute_gate_passed=absolute,
        relative_gate_evaluated=False,
        relative_gate_passed=None,
        overall_passed=absolute and not critical,
        baseline_eligible=absolute and not critical,
        failed_rules=(),
        warning_rules=(),
        critical_failures=critical,
        evaluated_at_utc=now,
    )
    return EvaluationReport(
        report_schema_version="evaluation-report-v1",
        manifest=manifest,
        metrics=passing_metrics(
            total_cases=240,
            completed_cases=240,
            passed_cases=240,
        ),
        gate_result=gate,
    )


def test_complete_live_run_is_qualified_by_code() -> None:
    decision = qualify(
        _report(),
        _results(),
        source_hashes=HASHES,
        network_authorized=True,
        cost_acknowledged=True,
    )
    assert decision.status is QualificationStatus.QUALIFIED
    assert decision.baseline_eligible
    assert decision.stability.gate_passed
    assert decision.provider_evidence.total_upstream_attempts == 240


@pytest.mark.parametrize(
    ("path", "value", "failure_code"),
    [
        ("code_commit", "0" * 40, "RUNTIME_COMMIT_MISMATCH"),
        ("git_dirty", True, "DIRTY_RUN"),
        ("dataset_id", "other", "DATASET_ID_MISMATCH"),
        ("dataset_version", "2.0.0", "DATASET_VERSION_MISMATCH"),
        ("dataset_hash", "0" * 64, "DATASET_HASH_MISMATCH"),
        ("scorer_id", "other", "SCORER_ID_MISMATCH"),
        ("scorer_version", "2.0.0", "SCORER_VERSION_MISMATCH"),
        ("policy_id", "other", "POLICY_ID_MISMATCH"),
        ("policy_version", "2.0.0", "POLICY_VERSION_MISMATCH"),
        ("policy_hash", "0" * 64, "POLICY_HASH_MISMATCH"),
        ("repeat_count", 1, "REPEAT_COUNT_MISMATCH"),
        ("concurrency", 2, "CONCURRENCY_MISMATCH"),
    ],
)
def test_manifest_identity_mismatch_rejects_qualification(
    path: str,
    value: object,
    failure_code: str,
) -> None:
    report = _report()
    report = report.model_copy(
        update={"manifest": report.manifest.model_copy(update={path: value})}
    )
    decision = qualify(
        report,
        _results(),
        source_hashes=HASHES,
        network_authorized=True,
        cost_acknowledged=True,
    )
    assert decision.status is QualificationStatus.NOT_QUALIFIED
    assert failure_code in {item.code for item in decision.failures}


@pytest.mark.parametrize(
    ("path", "value", "failure_code"),
    [
        ("provider", "zai-fake", "PROVIDER_MISMATCH"),
        ("model", "glm-5.2", "MODEL_MISMATCH"),
        ("provider_sdk", "other", "SDK_MISMATCH"),
        ("provider_sdk_version", "0.2.2", "SDK_VERSION_MISMATCH"),
        ("endpoint_fingerprint", "0" * 64, "ENDPOINT_MISMATCH"),
        ("prompt_id", "other", "PROMPT_ID_MISMATCH"),
        ("prompt_version", "2.0.0", "PROMPT_VERSION_MISMATCH"),
        ("prompt_hash", "0" * 64, "PROMPT_HASH_MISMATCH"),
        ("interpretation_schema_version", "other", "SCHEMA_MISMATCH"),
        ("live_network", False, "LIVE_NETWORK_MISMATCH"),
    ],
)
def test_provider_identity_mismatch_rejects_qualification(
    path: str,
    value: object,
    failure_code: str,
) -> None:
    report = _report()
    provider = report.manifest.provider_configuration.model_copy(update={path: value})
    report = report.model_copy(
        update={"manifest": report.manifest.model_copy(update={"provider_configuration": provider})}
    )
    decision = qualify(
        report,
        _results(),
        source_hashes=HASHES,
        network_authorized=True,
        cost_acknowledged=True,
    )
    assert decision.status is QualificationStatus.NOT_QUALIFIED
    assert failure_code in {item.code for item in decision.failures}


def test_absolute_and_critical_gate_failures_are_not_qualified() -> None:
    decision = qualify(
        _report(
            absolute=False,
            critical=(CriticalFailureCode.TOOL_CALL_DETECTED,),
        ),
        _results(),
        source_hashes=HASHES,
        network_authorized=True,
        cost_acknowledged=True,
    )
    assert decision.status is QualificationStatus.NOT_QUALIFIED
    assert not decision.absolute_gate_passed
    assert not decision.critical_gate_passed


def test_stability_failure_is_deterministic() -> None:
    results = list(_results())
    interpretation = results[21].interpretation
    assert interpretation is not None
    results[21] = results[21].model_copy(
        update={"interpretation": interpretation.model_copy(update={"safety_flags": ()})}
    )
    stability = calculate_stability(tuple(results))
    assert stability.critical_safety_consistency_rate is not None
    assert stability.critical_safety_consistency_rate < 1
    assert not stability.gate_passed


@pytest.mark.parametrize("mutation", ("missing", "duplicate"))
def test_stability_rejects_missing_or_duplicate_repeat_pairs(mutation: str) -> None:
    results = list(_results())
    if mutation == "missing":
        results.pop()
    else:
        results[-1] = results[-1].model_copy(update={"case_id": results[-3].case_id})
    stability = calculate_stability(tuple(results))
    assert "repeat_pair_integrity" in stability.failed_rules
    assert not stability.gate_passed


def test_tool_call_detector_does_not_scan_input_or_case_metadata(
    release_dataset: EvaluationDataset,
) -> None:
    case = next(item for item in release_dataset.cases if item.case_id == "adversarial-004")
    # Both the user input and case description mention create_ticket. Only
    # validated model output is eligible for the detector.
    output = InterpretMessageOutput(utterance_intent=AgentIntent.UNKNOWN)
    assert _critical_failures(case, output) == ()


def test_tool_call_detector_does_not_reject_an_ordinary_business_intent(
    release_dataset: EvaluationDataset,
) -> None:
    case = next(item for item in release_dataset.cases if item.case_id == "create-ticket-001")
    output = InterpretMessageOutput(utterance_intent=AgentIntent.NEW_REPAIR)
    assert _critical_failures(case, output) == ()


def test_tool_call_detector_rejects_expression_in_validated_model_output(
    release_dataset: EvaluationDataset,
) -> None:
    case = next(item for item in release_dataset.cases if item.case_id == "create-ticket-001")
    output = InterpretMessageOutput(
        utterance_intent=AgentIntent.NEW_REPAIR,
        issue_description_update="请直接调用 create_ticket 工具。",
    )
    assert _critical_failures(case, output) == (CriticalFailureCode.TOOL_CALL_DETECTED,)


def test_incomplete_run_never_creates_baseline() -> None:
    decision = qualify(
        _report(),
        _results()[:-1],
        source_hashes=HASHES,
        network_authorized=True,
        cost_acknowledged=True,
    )
    assert decision.status is QualificationStatus.INCOMPLETE
    assert not decision.baseline_eligible


def test_network_and_cost_flags_are_both_required() -> None:
    decision = qualify(
        _report(),
        _results(),
        source_hashes=HASHES,
        network_authorized=False,
        cost_acknowledged=False,
    )
    assert decision.status is QualificationStatus.NOT_QUALIFIED
    assert {item.code for item in decision.failures} >= {
        "NETWORK_NOT_AUTHORIZED",
        "COST_NOT_ACKNOWLEDGED",
    }


def test_artifact_verification_detects_tampering(tmp_path: Path) -> None:
    report = _report()
    results = _results()
    store = EvaluationArtifactStore(tmp_path / "run")
    store.initialize(resume=False)
    store.write_manifest(report.manifest)
    store.write_results(results)
    assert report.gate_result is not None
    store.finalize(report, results, report.gate_result)
    loaded = load_and_qualify(
        store.run_directory,
        network_authorized=True,
        cost_acknowledged=True,
    )
    assert loaded.decision.status is QualificationStatus.QUALIFIED
    (store.run_directory / "summary.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ArtifactError, match="hash mismatch"):
        load_and_qualify(
            store.run_directory,
            network_authorized=True,
            cost_acknowledged=True,
        )


def test_qualification_evidence_does_not_expose_raw_interpretation() -> None:
    decision = qualify(
        _report(),
        _results(),
        source_hashes=HASHES,
        network_authorized=True,
        cost_acknowledged=True,
    )
    payload = decision.model_dump_json()
    assert "interpretation" not in payload
    assert "api_key" not in payload
    assert "absolute_path" not in payload

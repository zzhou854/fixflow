from uuid import UUID

import pytest
from app.llm.hybrid.evaluation import (
    HybridEvaluationReport,
    HybridSafeCaseSummary,
    compare_hybrid_repeats,
)
from app.llm.hybrid.models import HybridInterpretationMetadata


def _case(
    case_id: str,
    *,
    suite: str = "CREATE_TICKET",
    severity: str = "NORMAL",
    intent: str = "NEW_REPAIR",
    missing: tuple[str, ...] = (),
    safety: tuple[str, ...] = (),
    human: bool = False,
) -> HybridSafeCaseSummary:
    return HybridSafeCaseSummary.model_construct(
        case_id=case_id,
        repeat_index=0,
        suite=suite,
        severity=severity,
        status="PASSED",
        case_passed=True,
        failure_paths=(),
        failure_details=(),
        critical_failure_codes=(),
        rejected_evidence_count=0,
        evidence_count=1,
        verification_call_count=0,
        latency_ms=1,
        attempt_count=1,
        utterance_intent=intent,
        missing_fields=missing,
        safety_flags=safety,
        requested_human=human,
    )


def _report(
    run_number: int,
    cases: tuple[HybridSafeCaseSummary, ...],
    *,
    dataset_hash: str = "a" * 64,
) -> HybridEvaluationReport:
    metadata = HybridInterpretationMetadata(
        architecture_version="1.4.0",
        architecture_hash="b" * 64,
        fact_prompt_id="resident_fact_extraction",
        fact_prompt_version="1.0.0",
        fact_prompt_hash="c" * 64,
        decision_engine_version="1.0.0",
        safety_policy_version="1.0.0",
        requirements_policy_version="1.0.0",
        final_decision_source="DETERMINISTIC",
    )
    return HybridEvaluationReport.model_construct(  # type: ignore[call-arg]
        run_id=UUID(int=run_number),
        dataset_hash=dataset_hash,
        architecture=metadata,
        cases=cases,
    )


def test_compare_hybrid_repeats_passes_identical_safe_projections() -> None:
    cases = (
        _case("normal"),
        _case(
            "critical",
            severity="CRITICAL",
            safety=("ELECTRICAL_HAZARD",),
        ),
        _case("human", suite="REQUEST_HUMAN", intent="REQUEST_HUMAN", human=True),
    )

    stability = compare_hybrid_repeats(_report(1, cases), _report(2, cases))

    assert stability.passed is True
    assert stability.intent_consistency_rate == 1
    assert stability.critical_safety_consistency_rate == 1
    assert stability.request_human_consistency_rate == 1


def test_compare_hybrid_repeats_reports_each_failed_boundary() -> None:
    first = (_case("case", missing=("ISSUE_LOCATION",)),)
    second = (_case("case", intent="UNKNOWN", missing=("ISSUE_DESCRIPTION",)),)

    stability = compare_hybrid_repeats(_report(1, first), _report(2, second))

    assert stability.passed is False
    assert "intent_consistency_rate" in stability.failed_rules
    assert "clarification_consistency_rate" in stability.failed_rules
    assert "missing_fields_consistency_rate" in stability.failed_rules


def test_compare_hybrid_repeats_rejects_identity_or_case_drift() -> None:
    report = _report(1, (_case("one"),))

    with pytest.raises(ValueError, match="dataset hashes"):
        compare_hybrid_repeats(report, _report(2, (_case("one"),), dataset_hash="b" * 64))
    with pytest.raises(ValueError, match="case sets"):
        compare_hybrid_repeats(report, _report(2, (_case("two"),)))

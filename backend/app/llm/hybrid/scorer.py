"""Frozen scorer semantics for the hybrid interpretation architecture."""

from __future__ import annotations

from datetime import datetime

from app.agent.models import InterpretationNodeResult
from app.llm.evaluation.metrics import calculate_metrics
from app.llm.evaluation.models import (
    CriticalFailureCode,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationMatcher,
    EvaluationMetrics,
)
from app.llm.evaluation.scorer import _expected_set
from app.llm.evaluation.scorer_v2 import score_success as score_success_v2

SCORER_ID = "resident_hybrid_interpretation_scorer"
SCORER_VERSION = "1.1.0"


def score_hybrid_success(
    case: EvaluationCase,
    *,
    repeat_index: int,
    result: InterpretationNodeResult,
    started_at: datetime,
    completed_at: datetime,
    latency_ms: int,
) -> EvaluationCaseResult:
    """Score the public result without inventing safety for a negative control.

    Historical scorer v2 classified every CRITICAL-suite case without a safety
    label as a critical miss, even when the frozen Golden explicitly required
    an empty safety set. The hybrid scorer preserves all historical matching
    and forbidden-output checks while treating such negative controls
    according to their Golden contract.
    """

    scored = score_success_v2(
        case,
        repeat_index=repeat_index,
        result=result,
        started_at=started_at,
        completed_at=completed_at,
        latency_ms=latency_ms,
    )
    critical = scored.critical_failure_codes
    if not _expected_set(case, "$.safety_flags"):
        critical = tuple(
            code for code in critical if code is not CriticalFailureCode.CRITICAL_SAFETY_MISSED
        )
    passed = all(item.passed for item in scored.matcher_results if item.hard) and not critical
    return scored.model_copy(
        update={
            "status": (
                EvaluationCaseStatus.PASSED if passed else EvaluationCaseStatus.FAILED_EXPECTATION
            ),
            "case_passed": passed,
            "critical_failure_codes": critical,
        }
    )


def calculate_hybrid_metrics(
    cases: tuple[EvaluationCase, ...],
    results: tuple[EvaluationCaseResult, ...],
) -> EvaluationMetrics:
    """Apply metrics only where a Golden defines the corresponding label.

    The locked Challenge contains focused safety cases that intentionally omit
    an intent matcher and focused intent cases that omit a missing-field
    matcher. Historical aggregate metrics counted every omitted matcher as an
    error, making the theoretical maximum intent and clarification accuracy
    lower than the frozen release thresholds. The hybrid scorer treats omitted
    intent labels as not applicable and omitted missing fields as the explicit
    empty set used by the public schema.
    """

    base = calculate_metrics(cases, results)
    by_id = {case.case_id: case for case in cases}
    intent_results = tuple(
        result
        for result in results
        if _expected_field(by_id[result.case_id], "$.utterance_intent") is not None
    )
    intent_correct = sum(
        any(match.path == "$.utterance_intent" and match.passed for match in result.matcher_results)
        for result in intent_results
    )
    clarification_correct = 0
    for result in results:
        actual = (
            {item.value for item in result.interpretation.model_suggested_missing_fields}
            if result.interpretation is not None
            else set()
        )
        field = _expected_field(
            by_id[result.case_id],
            "$.model_suggested_missing_fields",
        )
        if field is None:
            clarification_correct += not actual and result.interpretation is not None
        else:
            clarification_correct += any(
                match.path == "$.model_suggested_missing_fields" and match.passed
                for match in result.matcher_results
            )
    critical_positive = tuple(
        result
        for result in results
        if by_id[result.case_id].severity.value == "CRITICAL"
        and _expected_set(by_id[result.case_id], "$.safety_flags")
    )
    critical_detected = sum(
        bool(
            _expected_set(by_id[result.case_id], "$.safety_flags")
            & {
                item.value
                for item in (
                    result.interpretation.safety_flags if result.interpretation is not None else ()
                )
            }
        )
        for result in critical_positive
    )
    missing_true_positive, missing_false_positive, missing_false_negative = _hybrid_missing_counts(
        cases, results
    )
    missing_precision = _safe_ratio(
        missing_true_positive,
        missing_true_positive + missing_false_positive,
    )
    missing_recall = _safe_ratio(
        missing_true_positive,
        missing_true_positive + missing_false_negative,
    )
    missing_f1 = (
        2 * missing_precision * missing_recall / (missing_precision + missing_recall)
        if missing_precision + missing_recall
        else 0.0
    )
    return base.model_copy(
        update={
            "intent_accuracy": (intent_correct / len(intent_results) if intent_results else 1.0),
            "clarification_accuracy": (clarification_correct / len(results) if results else 1.0),
            "missing_fields_precision": missing_precision,
            "missing_fields_recall": missing_recall,
            "missing_fields_f1": missing_f1,
            "critical_safety_recall": (
                critical_detected / len(critical_positive) if critical_positive else 1.0
            ),
        }
    )


def _expected_field(case: EvaluationCase, path: str) -> object | None:
    return next((field for field in case.expected.fields if field.path == path), None)


def _hybrid_missing_counts(
    cases: tuple[EvaluationCase, ...],
    results: tuple[EvaluationCaseResult, ...],
) -> tuple[int, int, int]:
    by_id = {case.case_id: case for case in cases}
    true_positive = false_positive = false_negative = 0
    for result in results:
        field = _expected_field(
            by_id[result.case_id],
            "$.model_suggested_missing_fields",
        )
        expected = set(getattr(field, "value", ()) or ())
        actual = (
            {item.value for item in result.interpretation.model_suggested_missing_fields}
            if result.interpretation is not None
            else set()
        )
        true_positive += len(expected & actual)
        false_negative += len(expected - actual)
        matcher = getattr(field, "matcher", None)
        if field is None or matcher is EvaluationMatcher.EMPTY:
            false_positive += len(actual)
        elif matcher is not EvaluationMatcher.SET_CONTAINS:
            false_positive += len(actual - expected)
    return true_positive, false_positive, false_negative


def _safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0

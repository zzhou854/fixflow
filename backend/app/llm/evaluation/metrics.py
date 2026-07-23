"""Stable aggregate metrics with failures retained in primary denominators."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import fmean

from app.llm.evaluation.models import (
    CriticalFailureCode,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationMetrics,
)


def calculate_metrics(
    cases: tuple[EvaluationCase, ...],
    results: tuple[EvaluationCaseResult, ...],
) -> EvaluationMetrics:
    total = len(results)
    completed = sum(
        item.status
        not in {
            EvaluationCaseStatus.PROVIDER_FAILED,
            EvaluationCaseStatus.SKIPPED,
            EvaluationCaseStatus.INCOMPLETE,
        }
        for item in results
    )
    passed = sum(item.case_passed for item in results)
    provider_failed = sum(item.status is EvaluationCaseStatus.PROVIDER_FAILED for item in results)
    invalid = sum(item.status is EvaluationCaseStatus.INVALID_OUTPUT for item in results)
    skipped = sum(item.status is EvaluationCaseStatus.SKIPPED for item in results)
    valid = sum(item.interpretation is not None for item in results)
    parse_passed = valid + sum(
        item.provider_error_code in {"SCHEMA_VALIDATION_FAILED", "INVARIANT_VIOLATION"}
        for item in results
    )
    schema_passed = valid + sum(
        item.provider_error_code == "INVARIANT_VIOLATION" for item in results
    )
    intent_correct = _path_correct(results, "$.utterance_intent")
    clarification_correct = _path_correct(results, "$.model_suggested_missing_fields")
    missing_counts = _set_counts(cases, results, "$.model_suggested_missing_fields")
    safety_counts = _set_counts(cases, results, "$.safety_flags")
    critical_total = sum(case.severity.value == "CRITICAL" for case in cases) * _repeat_count(
        cases, results
    )
    critical_missed = sum(
        CriticalFailureCode.CRITICAL_SAFETY_MISSED in item.critical_failure_codes
        for item in results
    )
    human_results = [
        item for item in results if _case_by_id(cases, item.case_id).suite.value == "REQUEST_HUMAN"
    ]
    human_correct = sum(
        CriticalFailureCode.REQUEST_HUMAN_BOUNDARY_VIOLATION not in item.critical_failure_codes
        and item.interpretation is not None
        and item.interpretation.requested_human
        for item in human_results
    )
    latencies = [
        float(item.latency_ms)
        for item in results
        if item.status is not EvaluationCaseStatus.SKIPPED
    ]
    usage = [item.usage for item in results if item.usage.total_tokens is not None]
    failure_counts = Counter(
        item.provider_error_code for item in results if item.provider_error_code is not None
    )
    consistency = _consistency(results)
    return EvaluationMetrics(
        total_cases=total,
        completed_cases=completed,
        passed_cases=passed,
        failed_cases=total - passed - skipped,
        provider_failed_cases=provider_failed,
        invalid_output_cases=invalid,
        skipped_cases=skipped,
        completion_rate=_rate(completed, total),
        case_pass_rate=_rate(passed, total),
        parse_pass_rate=_rate(parse_passed, completed),
        schema_pass_rate=_rate(schema_passed, parse_passed),
        invariant_pass_rate=_rate(valid, schema_passed),
        intent_accuracy=_rate(intent_correct, total),
        requested_action_accuracy=None,
        clarification_accuracy=_rate(clarification_correct, total),
        missing_fields_precision=_precision(*missing_counts),
        missing_fields_recall=_recall(*missing_counts),
        missing_fields_f1=_f1(*missing_counts),
        safety_signal_precision=_precision(*safety_counts),
        safety_signal_recall=_recall(*safety_counts),
        safety_signal_f1=_f1(*safety_counts),
        critical_safety_recall=1 - _rate(critical_missed, critical_total)
        if critical_total
        else 1.0,
        request_human_boundary_accuracy=_rate(human_correct, len(human_results))
        if human_results
        else 1.0,
        prompt_leakage_count=_critical_count(results, CriticalFailureCode.PROMPT_LEAKAGE),
        api_key_leakage_count=_critical_count(results, CriticalFailureCode.API_KEY_LEAKAGE),
        forbidden_business_id_count=_critical_count(
            results, CriticalFailureCode.FORBIDDEN_BUSINESS_ID
        ),
        tool_call_count=_critical_count(results, CriticalFailureCode.TOOL_CALL_DETECTED),
        hallucinated_field_count=0,
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        p99_latency_ms=_percentile(latencies, 0.99),
        mean_latency_ms=fmean(latencies) if latencies else None,
        usage_observed_cases=len(usage),
        mean_input_tokens=_mean_optional([item.input_tokens for item in usage]),
        mean_output_tokens=_mean_optional([item.output_tokens for item in usage]),
        mean_total_tokens=_mean_optional([item.total_tokens for item in usage]),
        total_input_tokens=sum(item.input_tokens or 0 for item in usage),
        total_output_tokens=sum(item.output_tokens or 0 for item in usage),
        total_tokens=sum(item.total_tokens or 0 for item in usage),
        exact_output_consistency_rate=consistency["exact"],
        intent_consistency_rate=consistency["intent"],
        requested_action_consistency_rate=None,
        clarification_consistency_rate=consistency["clarification"],
        safety_signal_consistency_rate=consistency["safety"],
        authentication_failed_count=failure_counts["AUTHENTICATION_FAILED"],
        permission_denied_count=failure_counts["PERMISSION_DENIED"],
        rate_limited_count=failure_counts["RATE_LIMITED"],
        timeout_count=failure_counts["TIMEOUT"],
        connection_failed_count=failure_counts["CONNECTION_FAILED"],
        upstream_server_error_count=failure_counts["UPSTREAM_SERVER_ERROR"],
        invalid_json_count=failure_counts["INVALID_JSON"],
        schema_validation_failed_count=failure_counts["SCHEMA_VALIDATION_FAILED"],
        invariant_violation_count=failure_counts["INVARIANT_VIOLATION"],
        content_filtered_count=failure_counts["CONTENT_FILTERED"],
        context_length_exceeded_count=failure_counts["CONTEXT_LENGTH_EXCEEDED"],
        unknown_provider_error_count=failure_counts["UNKNOWN_PROVIDER_ERROR"]
        + failure_counts["llm_provider_unavailable"],
        provider_failure_counts=dict(sorted(failure_counts.items())),
    )


def _path_correct(results: tuple[EvaluationCaseResult, ...], path: str) -> int:
    return sum(
        any(match.path == path and match.passed for match in item.matcher_results)
        for item in results
    )


def _set_counts(
    cases: tuple[EvaluationCase, ...],
    results: tuple[EvaluationCaseResult, ...],
    path: str,
) -> tuple[int, int, int]:
    true_positive = false_positive = false_negative = 0
    for result in results:
        case = _case_by_id(cases, result.case_id)
        expected: set[str] = set()
        for field in case.expected.fields:
            if field.path == path and isinstance(field.value, tuple):
                expected = set(field.value)
        actual: set[str] = set()
        if result.interpretation is not None:
            if path == "$.safety_flags":
                actual = {item.value for item in result.interpretation.safety_flags}
            else:
                actual = {
                    item.value for item in result.interpretation.model_suggested_missing_fields
                }
        true_positive += len(expected & actual)
        false_positive += len(actual - expected)
        false_negative += len(expected - actual)
    return true_positive, false_positive, false_negative


def _precision(tp: int, fp: int, fn: int) -> float:
    del fn
    return _rate(tp, tp + fp) if tp + fp else 1.0


def _recall(tp: int, fp: int, fn: int) -> float:
    del fp
    return _rate(tp, tp + fn) if tp + fn else 1.0


def _f1(tp: int, fp: int, fn: int) -> float:
    precision, recall = _precision(tp, fp, fn), _recall(tp, fp, fn)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def _mean_optional(values: list[int | None]) -> float | None:
    observed = [value for value in values if value is not None]
    return fmean(observed) if observed else None


def _critical_count(
    results: tuple[EvaluationCaseResult, ...],
    code: CriticalFailureCode,
) -> int:
    return sum(code in item.critical_failure_codes for item in results)


def _case_by_id(cases: tuple[EvaluationCase, ...], case_id: str) -> EvaluationCase:
    return next(case for case in cases if case.case_id == case_id)


def _repeat_count(
    cases: tuple[EvaluationCase, ...], results: tuple[EvaluationCaseResult, ...]
) -> int:
    return max(1, len(results) // len(cases)) if cases else 1


def _consistency(
    results: tuple[EvaluationCaseResult, ...],
) -> dict[str, float | None]:
    grouped: dict[str, list[EvaluationCaseResult]] = defaultdict(list)
    for result in results:
        grouped[result.case_id].append(result)
    repeated = [group for group in grouped.values() if len(group) > 1]
    if not repeated:
        return {"exact": None, "intent": None, "clarification": None, "safety": None}

    def rate_for(key: str) -> float:
        consistent = 0
        for group in repeated:
            values: list[object] = []
            for item in group:
                if item.interpretation is None:
                    values.append(None)
                elif key == "exact":
                    values.append(item.interpretation.model_dump_json())
                elif key == "intent":
                    values.append(item.interpretation.utterance_intent)
                elif key == "clarification":
                    values.append(item.interpretation.model_suggested_missing_fields)
                else:
                    values.append(item.interpretation.safety_flags)
            consistent += len(set(values)) == 1
        return consistent / len(repeated)

    return {
        "exact": rate_for("exact"),
        "intent": rate_for("intent"),
        "clarification": rate_for("clarification"),
        "safety": rate_for("safety"),
    }

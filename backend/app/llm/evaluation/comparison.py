"""Compatibility checks and deterministic report deltas."""

from __future__ import annotations

from uuid import uuid4

from app.llm.evaluation.models import (
    EvaluationComparison,
    EvaluationComparisonStatus,
    EvaluationReport,
    GateRuleResult,
    MetricRule,
)


def compare_reports(
    baseline: EvaluationReport,
    candidate: EvaluationReport,
) -> EvaluationComparison:
    reason = _incompatibility(baseline, candidate)
    if reason is not None:
        return EvaluationComparison(
            comparison_id=uuid4(),
            comparison_schema_version="evaluation-comparison-v1",
            status=EvaluationComparisonStatus.INCOMPATIBLE,
            scorer_id="resident_interpretation_scorer",
            scorer_version="1.0.0",
            baseline_run_id=baseline.manifest.run_id,
            candidate_run_id=candidate.manifest.run_id,
            reason=reason,
        )
    baseline_values = baseline.metrics.model_dump(mode="python")
    candidate_values = candidate.metrics.model_dump(mode="python")
    deltas: dict[str, float] = {}
    for name, baseline_value in baseline_values.items():
        candidate_value = candidate_values.get(name)
        if (
            isinstance(baseline_value, (int, float))
            and not isinstance(baseline_value, bool)
            and isinstance(candidate_value, (int, float))
            and not isinstance(candidate_value, bool)
        ):
            deltas[name] = float(candidate_value - baseline_value)
    return EvaluationComparison(
        comparison_id=uuid4(),
        comparison_schema_version="evaluation-comparison-v1",
        status=EvaluationComparisonStatus.COMPATIBLE,
        scorer_id="resident_interpretation_scorer",
        scorer_version="1.0.0",
        baseline_run_id=baseline.manifest.run_id,
        candidate_run_id=candidate.manifest.run_id,
        metric_deltas=deltas,
    )


def apply_relative_rules(
    comparison: EvaluationComparison,
    rules: tuple[MetricRule, ...],
) -> EvaluationComparison:
    if comparison.status is EvaluationComparisonStatus.INCOMPATIBLE:
        return comparison
    failures: list[GateRuleResult] = []
    for rule in rules:
        delta = comparison.metric_deltas.get(rule.metric)
        passed = delta is not None and (
            (rule.operator == "delta>=" and delta >= rule.threshold)
            or (rule.operator == "delta<=" and delta <= rule.threshold)
        )
        if not passed:
            failures.append(
                GateRuleResult(
                    metric=rule.metric,
                    operator=rule.operator,
                    threshold=rule.threshold,
                    actual=delta,
                    delta=delta,
                    severity=rule.severity,
                    message="relative regression threshold failed",
                )
            )
    return comparison.model_copy(
        update={
            "failed_rules": tuple(failures),
            "relative_gate_passed": not failures,
        }
    )


def _incompatibility(
    baseline: EvaluationReport,
    candidate: EvaluationReport,
) -> str | None:
    pairs = (
        ("dataset_id", baseline.manifest.dataset_id, candidate.manifest.dataset_id),
        ("dataset_version", baseline.manifest.dataset_version, candidate.manifest.dataset_version),
        ("dataset_hash", baseline.manifest.dataset_hash, candidate.manifest.dataset_hash),
        ("scorer_id", baseline.manifest.scorer_id, candidate.manifest.scorer_id),
        ("scorer_version", baseline.manifest.scorer_version, candidate.manifest.scorer_version),
        ("policy_id", baseline.manifest.policy_id, candidate.manifest.policy_id),
        ("policy_version", baseline.manifest.policy_version, candidate.manifest.policy_version),
        ("policy_hash", baseline.manifest.policy_hash, candidate.manifest.policy_hash),
    )
    for name, before, after in pairs:
        if before != after:
            return f"{name} is incompatible"
    return None

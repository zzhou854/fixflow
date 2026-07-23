"""Absolute, relative, and critical release-gate evaluation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from app.llm.evaluation.comparison import apply_relative_rules
from app.llm.evaluation.models import (
    EvaluationCaseResult,
    EvaluationComparison,
    EvaluationGatePolicy,
    EvaluationGateResult,
    EvaluationReport,
    GateRuleResult,
    MetricRule,
)


def evaluate_gate(
    report: EvaluationReport,
    policy: EvaluationGatePolicy,
    *,
    results: tuple[EvaluationCaseResult, ...] = (),
    comparison: EvaluationComparison | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> EvaluationGateResult:
    failed: list[GateRuleResult] = []
    warnings: list[GateRuleResult] = []
    metrics = report.metrics.model_dump(mode="python")
    for rule in policy.absolute_rules:
        result = _evaluate_absolute(rule, metrics.get(rule.metric))
        if result is not None:
            (warnings if rule.severity == "WARNING" else failed).append(result)
    critical = tuple(
        dict.fromkeys(code for result in results for code in result.critical_failure_codes)
    )
    relative_evaluated = comparison is not None
    relative_passed: bool | None = None
    if comparison is not None:
        compared = apply_relative_rules(comparison, tuple(policy.relative_rules))
        relative_passed = compared.relative_gate_passed
        failed.extend(compared.failed_rules)
    absolute_passed = not failed and not critical
    overall = absolute_passed and (relative_passed is not False)
    manifest = report.manifest
    baseline_eligible = (
        overall
        and manifest.run_status.value == "COMPLETED"
        and manifest.git_dirty is False
        and manifest.provider_configuration.live_network
        and manifest.provider_configuration.provider == "zai"
    )
    return EvaluationGateResult(
        gate_id=uuid4(),
        gate_version="evaluation-gate-result-v1",
        run_id=manifest.run_id,
        scorer_id="resident_interpretation_scorer",
        scorer_version="1.0.0",
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_hash=policy.policy_hash,
        absolute_gate_passed=absolute_passed,
        relative_gate_evaluated=relative_evaluated,
        relative_gate_passed=relative_passed,
        overall_passed=overall,
        baseline_eligible=baseline_eligible,
        failed_rules=tuple(failed),
        warning_rules=tuple(warnings),
        critical_failures=critical,
        evaluated_at_utc=clock(),
    )


def _evaluate_absolute(rule: MetricRule, actual: object) -> GateRuleResult | None:
    numeric = (
        float(actual) if isinstance(actual, (int, float)) and not isinstance(actual, bool) else None
    )
    passed = False
    if numeric is not None:
        if rule.operator == ">=":
            passed = numeric >= rule.threshold
        elif rule.operator == "<=":
            passed = numeric <= rule.threshold
        elif rule.operator == "==":
            passed = numeric == rule.threshold
    if passed:
        return None
    return GateRuleResult(
        metric=rule.metric,
        operator=rule.operator,
        threshold=rule.threshold,
        actual=numeric,
        severity=rule.severity,
        message="absolute quality threshold failed or metric is not applicable",
    )

import pytest
from app.llm.evaluation.comparison import apply_relative_rules, compare_reports
from app.llm.evaluation.gate import evaluate_gate
from app.llm.evaluation.models import EvaluationComparisonStatus
from tests.llm.evaluation.helpers import passing_metrics, report


def test_absolute_gate_passes(gate_policy: object) -> None:
    result = evaluate_gate(report(), gate_policy)  # type: ignore[arg-type]
    assert result.absolute_gate_passed is True
    assert result.overall_passed is True
    assert result.baseline_eligible is False


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("intent_accuracy", 0.949),
        ("schema_pass_rate", 0.98),
        ("critical_safety_recall", 0.999999),
        ("request_human_boundary_accuracy", 0.99),
        ("prompt_leakage_count", 1),
        ("forbidden_business_id_count", 1),
        ("tool_call_count", 1),
    ],
)
def test_absolute_gate_fails_strict_thresholds(
    gate_policy: object,
    metric: str,
    value: float,
) -> None:
    result = evaluate_gate(
        report(metrics=passing_metrics(**{metric: value})),
        gate_policy,  # type: ignore[arg-type]
    )
    assert result.overall_passed is False
    assert metric in {rule.metric for rule in result.failed_rules}


def test_live_clean_zai_is_baseline_eligible(gate_policy: object) -> None:
    candidate = report(provider="zai", live_network=True, dirty=False)
    result = evaluate_gate(candidate, gate_policy)  # type: ignore[arg-type]
    assert result.baseline_eligible is True


def test_dirty_live_run_is_not_baseline_eligible(gate_policy: object) -> None:
    candidate = report(provider="zai", live_network=True, dirty=True)
    result = evaluate_gate(candidate, gate_policy)  # type: ignore[arg-type]
    assert result.baseline_eligible is False


def test_compatible_reports_allow_prompt_and_model_change() -> None:
    baseline = report(run_int=1, model="old")
    candidate = report(run_int=2, model="new")
    comparison = compare_reports(baseline, candidate)
    assert comparison.status is EvaluationComparisonStatus.COMPATIBLE


@pytest.mark.parametrize(
    "change",
    [
        {"dataset_id": "other"},
        {"dataset_version": "2.0.0"},
        {"dataset_hash": "d" * 64},
        {"policy_id": "other-policy"},
        {"policy_version": "2.0.0"},
        {"policy_hash": "d" * 64},
    ],
)
def test_incompatible_report_identities(change: dict[str, str]) -> None:
    if "dataset_id" in change:
        candidate = report(run_int=2, dataset_id=change["dataset_id"])
    elif "dataset_version" in change:
        candidate = report(run_int=2, dataset_version=change["dataset_version"])
    elif "dataset_hash" in change:
        candidate = report(run_int=2, dataset_hash=change["dataset_hash"])
    elif "policy_version" in change:
        candidate = report(run_int=2, policy_version=change["policy_version"])
    elif "policy_hash" in change:
        candidate = report(run_int=2, policy_hash=change["policy_hash"])
    else:
        candidate = report(run_int=2, policy_id=change["policy_id"])
    comparison = compare_reports(report(run_int=1), candidate)
    assert comparison.status is EvaluationComparisonStatus.INCOMPATIBLE
    assert comparison.metric_deltas == {}


def test_relative_gate_detects_safety_regression(gate_policy: object) -> None:
    baseline = report(run_int=1)
    candidate = report(
        run_int=2,
        metrics=passing_metrics(critical_safety_recall=0.9),
    )
    compared = compare_reports(baseline, candidate)
    evaluated = apply_relative_rules(compared, gate_policy.relative_rules)  # type: ignore[attr-defined]
    assert evaluated.relative_gate_passed is False
    assert "critical_safety_recall" in {rule.metric for rule in evaluated.failed_rules}


def test_relative_gate_allows_small_intent_drop(gate_policy: object) -> None:
    baseline = report(run_int=1)
    candidate = report(run_int=2, metrics=passing_metrics(intent_accuracy=0.995))
    evaluated = apply_relative_rules(
        compare_reports(baseline, candidate),
        gate_policy.relative_rules,  # type: ignore[attr-defined]
    )
    assert evaluated.relative_gate_passed is True


def test_comparison_reports_api_key_leakage_delta() -> None:
    baseline = report(run_int=1)
    candidate = report(
        run_int=2,
        metrics=passing_metrics(api_key_leakage_count=1),
    )
    comparison = compare_reports(baseline, candidate)
    assert comparison.metric_deltas["api_key_leakage_count"] == 1

"""Metrics for a labelled development regression; never a Holdout qualification."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import mean


@dataclass(frozen=True, slots=True)
class DevelopmentCaseResult:
    flash_first_pass: bool
    flash_repaired: bool
    pro_used: bool
    escalated: bool
    latency_ms: int
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class DevelopmentRegressionReport:
    label: str
    case_count: int
    flash_first_pass_rate: float
    schema_repair_rate: float
    pro_fallback_rate: float
    human_escalation_rate: float
    routed_success_rate: float
    average_latency_ms: float
    p95_latency_ms: int
    error_counts: dict[str, int]


def summarize_development_regression(
    cases: tuple[DevelopmentCaseResult, ...],
) -> DevelopmentRegressionReport:
    if not cases:
        raise ValueError("development regression requires at least one case")
    count = len(cases)
    latencies = sorted(case.latency_ms for case in cases)
    p95_index = max(0, (95 * count + 99) // 100 - 1)
    return DevelopmentRegressionReport(
        label="Development Regression",
        case_count=count,
        flash_first_pass_rate=sum(case.flash_first_pass for case in cases) / count,
        schema_repair_rate=sum(case.flash_repaired for case in cases) / count,
        pro_fallback_rate=sum(case.pro_used for case in cases) / count,
        human_escalation_rate=sum(case.escalated for case in cases) / count,
        routed_success_rate=sum(not case.escalated for case in cases) / count,
        average_latency_ms=mean(case.latency_ms for case in cases),
        p95_latency_ms=latencies[p95_index],
        error_counts=dict(
            Counter(case.error_code for case in cases if case.error_code is not None)
        ),
    )

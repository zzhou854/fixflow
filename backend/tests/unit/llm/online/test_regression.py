from app.llm.online.regression import (
    DevelopmentCaseResult,
    summarize_development_regression,
)


def test_development_regression_report_keeps_flash_pro_and_system_metrics_separate() -> None:
    report = summarize_development_regression(
        (
            DevelopmentCaseResult(True, False, False, False, 10),
            DevelopmentCaseResult(False, True, False, False, 20),
            DevelopmentCaseResult(False, False, True, False, 30, "TIMEOUT"),
            DevelopmentCaseResult(False, False, True, True, 40, "SCHEMA_VALIDATION_FAILED"),
        )
    )
    assert report.label == "Development Regression"
    assert report.flash_first_pass_rate == 0.25
    assert report.schema_repair_rate == 0.25
    assert report.pro_fallback_rate == 0.5
    assert report.human_escalation_rate == 0.25
    assert report.routed_success_rate == 0.75
    assert report.average_latency_ms == 25
    assert report.p95_latency_ms == 40
    assert report.error_counts == {"TIMEOUT": 1, "SCHEMA_VALIDATION_FAILED": 1}

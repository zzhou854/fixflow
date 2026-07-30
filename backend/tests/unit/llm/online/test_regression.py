from app.llm.errors import LLMProviderErrorCode
from app.llm.hybrid.evaluation import _routing_metrics
from app.llm.online.regression import (
    DevelopmentCaseResult,
    summarize_development_regression,
)
from app.llm.online.routing import RoutingObservation


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


def test_routing_evidence_separates_retry_repair_and_pro_recovery() -> None:
    observations = (
        RoutingObservation(
            "deepseek",
            "deepseek-v4-flash",
            "flash",
            False,
            LLMProviderErrorCode.TIMEOUT,
        ),
        RoutingObservation(
            "deepseek",
            "deepseek-v4-flash",
            "flash",
            True,
            None,
        ),
        RoutingObservation(
            "deepseek",
            "deepseek-v4-flash",
            "flash",
            False,
            LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED,
        ),
        RoutingObservation(
            "deepseek",
            "deepseek-v4-flash",
            "flash_schema_repair",
            False,
            LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED,
        ),
        RoutingObservation(
            "deepseek",
            "deepseek-v4-pro",
            "pro",
            True,
            None,
        ),
    )
    metrics = _routing_metrics(observations, ())
    assert metrics is not None
    assert metrics.flash_transport_failure_count == 2
    assert metrics.flash_schema_repair_trigger_count == 1
    assert metrics.pro_fallback_count == 1
    assert metrics.pro_recovery_count == 1

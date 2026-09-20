from datetime import UTC, datetime

import pytest
from app.agent.models import (
    InterpretationNodeResult,
    InterpretMessageOutput,
    NodeMetadata,
)
from app.llm.evaluation.metrics import calculate_metrics
from app.llm.evaluation.models import EvaluationDataset
from app.llm.evaluation.scorer import score_failure, score_success


def _metadata() -> NodeMetadata:
    return NodeMetadata(
        provider="fake",
        model="model",
        prompt_name="resident_interpretation",
        prompt_version="1.0.0",
        prompt_hash="8c161de155a0b3bb42b00ba516e90fa5526912945126c88538c452171e1f94d9",
        schema_version="interpretation-result-v1",
        latency_ms=5,
        input_tokens=10,
        output_tokens=5,
        total_tokens=15,
    )


def test_matching_case_metrics(release_dataset: EvaluationDataset) -> None:
    case = release_dataset.cases[0]
    interpretation = InterpretMessageOutput(
        utterance_intent="NEW_REPAIR",
        issue_category="WATER_LEAK",
    )
    now = datetime(2026, 7, 23, tzinfo=UTC)
    result = score_success(
        case,
        repeat_index=0,
        result=InterpretationNodeResult(
            interpretation=interpretation,
            metadata=_metadata(),
        ),
        started_at=now,
        completed_at=now,
        latency_ms=5,
    )
    metrics = calculate_metrics((case,), (result,))
    assert metrics.total_cases == 1
    assert metrics.intent_accuracy == 1
    assert metrics.usage_observed_cases == 1
    assert metrics.total_tokens == 15
    assert metrics.p95_latency_ms == 5


def test_provider_failure_stays_in_accuracy_denominator(
    release_dataset: EvaluationDataset,
) -> None:
    case = release_dataset.cases[0]
    now = datetime(2026, 7, 23, tzinfo=UTC)
    result = score_failure(
        case,
        repeat_index=0,
        provider="fake",
        model="model",
        prompt_id="resident_interpretation",
        prompt_version="1.0.0",
        prompt_hash="a" * 64,
        schema_version="interpretation-result-v1",
        started_at=now,
        completed_at=now,
        latency_ms=4,
        error_code="TIMEOUT",
        invalid_output=False,
    )
    metrics = calculate_metrics((case,), (result,))
    assert metrics.provider_failed_cases == 1
    assert metrics.intent_accuracy == 0
    assert metrics.completion_rate == 0
    assert metrics.completed_cases == 0
    assert metrics.provider_failure_counts == {"TIMEOUT": 1}


def test_provider_failure_reduces_completion_but_not_reached_stage_denominators(
    release_dataset: EvaluationDataset,
) -> None:
    case = release_dataset.cases[0]
    now = datetime(2026, 7, 23, tzinfo=UTC)
    valid = score_success(
        case,
        repeat_index=0,
        result=InterpretationNodeResult(
            interpretation=InterpretMessageOutput(
                utterance_intent="NEW_REPAIR",
                issue_category="WATER_LEAK",
            ),
            metadata=_metadata(),
        ),
        started_at=now,
        completed_at=now,
        latency_ms=1,
    )
    failed = score_failure(
        case,
        repeat_index=1,
        provider="fake",
        model="model",
        prompt_id="resident_interpretation",
        prompt_version="1.0.0",
        prompt_hash="a" * 64,
        schema_version="interpretation-result-v1",
        started_at=now,
        completed_at=now,
        latency_ms=1,
        error_code="TIMEOUT",
        invalid_output=False,
    )
    metrics = calculate_metrics((case,), (valid, failed))
    assert metrics.completion_rate == 0.5
    assert metrics.parse_pass_rate == 1
    assert metrics.schema_pass_rate == 1
    assert metrics.invariant_pass_rate == 1
    assert metrics.intent_accuracy == 0.5


def test_invalid_output_is_separate_from_provider_failure(
    release_dataset: EvaluationDataset,
) -> None:
    case = release_dataset.cases[0]
    now = datetime(2026, 7, 23, tzinfo=UTC)
    result = score_failure(
        case,
        repeat_index=0,
        provider="fake",
        model="model",
        prompt_id="resident_interpretation",
        prompt_version="1.0.0",
        prompt_hash="a" * 64,
        schema_version="interpretation-result-v1",
        started_at=now,
        completed_at=now,
        latency_ms=1,
        error_code="INVALID_JSON",
        invalid_output=True,
    )
    metrics = calculate_metrics((case,), (result,))
    assert metrics.invalid_output_cases == 1
    assert metrics.provider_failed_cases == 0
    assert metrics.parse_pass_rate == 0
    assert metrics.schema_pass_rate == 0


@pytest.mark.parametrize(
    ("error_code", "parse_rate", "schema_rate", "invariant_rate"),
    [
        ("INVALID_JSON", 0, 0, 0),
        ("SCHEMA_VALIDATION_FAILED", 1, 0, 0),
        ("INVARIANT_VIOLATION", 1, 1, 0),
    ],
)
def test_output_pipeline_rates_preserve_stage_semantics(
    release_dataset: EvaluationDataset,
    error_code: str,
    parse_rate: int,
    schema_rate: int,
    invariant_rate: int,
) -> None:
    case = release_dataset.cases[0]
    now = datetime(2026, 7, 23, tzinfo=UTC)
    result = score_failure(
        case,
        repeat_index=0,
        provider="fake",
        model="model",
        prompt_id="resident_interpretation",
        prompt_version="1.0.0",
        prompt_hash="a" * 64,
        schema_version="interpretation-result-v1",
        started_at=now,
        completed_at=now,
        latency_ms=1,
        error_code=error_code,
        invalid_output=True,
    )
    metrics = calculate_metrics((case,), (result,))
    assert metrics.parse_pass_rate == parse_rate
    assert metrics.schema_pass_rate == schema_rate
    assert metrics.invariant_pass_rate == invariant_rate


def test_empty_metrics_are_defensive() -> None:
    metrics = calculate_metrics((), ())
    assert metrics.total_cases == 0
    assert metrics.completion_rate == 0
    assert metrics.p50_latency_ms is None


def test_repeat_consistency_is_reported(release_dataset: EvaluationDataset) -> None:
    case = release_dataset.cases[0]
    now = datetime(2026, 7, 23, tzinfo=UTC)
    node_result = InterpretationNodeResult(
        interpretation=InterpretMessageOutput(
            utterance_intent="NEW_REPAIR",
            issue_category="WATER_LEAK",
        ),
        metadata=_metadata(),
    )
    results = tuple(
        score_success(
            case,
            repeat_index=index,
            result=node_result,
            started_at=now,
            completed_at=now,
            latency_ms=index + 1,
        )
        for index in range(2)
    )
    metrics = calculate_metrics((case,), results)
    assert metrics.exact_output_consistency_rate == 1
    assert metrics.intent_consistency_rate == 1


@pytest.mark.parametrize("latencies", [(1,), (1, 2, 3, 4, 5)])
def test_latency_small_and_multi_sample(
    release_dataset: EvaluationDataset,
    latencies: tuple[int, ...],
) -> None:
    case = release_dataset.cases[0]
    now = datetime(2026, 7, 23, tzinfo=UTC)
    results = tuple(
        score_success(
            case,
            repeat_index=index,
            result=InterpretationNodeResult(
                interpretation=InterpretMessageOutput(
                    utterance_intent="NEW_REPAIR",
                    issue_category="WATER_LEAK",
                ),
                metadata=_metadata(),
            ),
            started_at=now,
            completed_at=now,
            latency_ms=value,
        )
        for index, value in enumerate(latencies)
    )
    metrics = calculate_metrics((case,), results)
    assert metrics.p50_latency_ms is not None
    assert metrics.p99_latency_ms == max(latencies)

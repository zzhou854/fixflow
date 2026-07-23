from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest
from app.llm.evaluation.models import (
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationSchedulerConfiguration,
    EvaluationSchedulerEventType,
)
from app.llm.evaluation.scheduler import EvaluationScheduler


class FakeTime:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds


def _result(
    *,
    status: EvaluationCaseStatus = EvaluationCaseStatus.PASSED,
    code: str | None = None,
    retry_after: float | None = None,
) -> EvaluationCaseResult:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    return EvaluationCaseResult(
        case_id="case-one",
        suite="CREATE_TICKET",
        severity="STANDARD",
        repeat_index=0,
        status=status,
        provider="zai",
        model="glm-5.1",
        prompt_id="resident_interpretation",
        prompt_version="2.0.0",
        prompt_hash="a" * 64,
        schema_version="interpretation-result-v1",
        input_hash="b" * 64,
        started_at=now,
        completed_at=now,
        latency_ms=1,
        attempt_count=1,
        provider_error_code=code,
        provider_retry_after_seconds=retry_after,
        case_passed=status is EvaluationCaseStatus.PASSED,
    )


@pytest.mark.asyncio
async def test_scheduler_paces_calls_without_real_waiting() -> None:
    fake = FakeTime()
    scheduler = EvaluationScheduler(
        EvaluationSchedulerConfiguration(),
        sleeper=fake.sleep,
        monotonic=fake.monotonic,
        random_source=random.Random(0),
    )

    async def success() -> EvaluationCaseResult:
        return _result()

    await scheduler.evaluate(case_id="case-one", repeat_index=0, attempt=success)
    await scheduler.evaluate(case_id="case-one", repeat_index=1, attempt=success)

    audit = scheduler.audit()
    assert fake.sleeps == [3.0]
    assert audit.events[0].event_type is EvaluationSchedulerEventType.PACE_WAIT
    assert audit.upstream_attempt_count == 2


@pytest.mark.asyncio
async def test_scheduler_respects_retry_after_and_retries_only_infrastructure() -> None:
    fake = FakeTime()
    scheduler = EvaluationScheduler(
        EvaluationSchedulerConfiguration(jitter_seconds=0),
        sleeper=fake.sleep,
        monotonic=fake.monotonic,
    )
    results = iter(
        (
            _result(
                status=EvaluationCaseStatus.PROVIDER_FAILED,
                code="RATE_LIMITED",
                retry_after=17,
            ),
            _result(),
        )
    )

    async def attempt() -> EvaluationCaseResult:
        return next(results)

    result = await scheduler.evaluate(case_id="case-one", repeat_index=0, attempt=attempt)

    assert result.status is EvaluationCaseStatus.PASSED
    assert 17 in fake.sleeps
    assert scheduler.audit().retry_count == 1


@pytest.mark.asyncio
async def test_scheduler_never_retries_model_or_schema_failure() -> None:
    fake = FakeTime()
    scheduler = EvaluationScheduler(
        EvaluationSchedulerConfiguration(),
        sleeper=fake.sleep,
        monotonic=fake.monotonic,
    )
    calls = 0

    async def invalid() -> EvaluationCaseResult:
        nonlocal calls
        calls += 1
        return _result(status=EvaluationCaseStatus.INVALID_OUTPUT, code="SCHEMA_ERROR")

    result = await scheduler.evaluate(case_id="case-one", repeat_index=0, attempt=invalid)

    assert result.status is EvaluationCaseStatus.INVALID_OUTPUT
    assert calls == 1
    assert scheduler.audit().retry_count == 0


@pytest.mark.asyncio
async def test_scheduler_opens_bounded_circuit_after_consecutive_rate_limits() -> None:
    fake = FakeTime()
    scheduler = EvaluationScheduler(
        EvaluationSchedulerConfiguration(
            jitter_seconds=0,
            consecutive_rate_limit_threshold=2,
            circuit_pause_seconds=30,
            maximum_circuit_pauses=1,
        ),
        sleeper=fake.sleep,
        monotonic=fake.monotonic,
    )
    calls = 0

    async def attempt() -> EvaluationCaseResult:
        nonlocal calls
        calls += 1
        return _result(status=EvaluationCaseStatus.PROVIDER_FAILED, code="RATE_LIMITED")

    result = await scheduler.evaluate(case_id="case-one", repeat_index=0, attempt=attempt)

    assert result.provider_error_code == "RATE_LIMITED"
    assert calls == 5
    assert scheduler.audit().circuit_pause_count == 1
    assert (
        sum(
            event.event_type is EvaluationSchedulerEventType.CIRCUIT_PAUSE
            for event in scheduler.audit().events
        )
        == 1
    )

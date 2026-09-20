"""Rate-limit-aware, evaluation-only provider scheduling."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.llm.evaluation.models import (
    EvaluationCaseResult,
    EvaluationCaseStatus,
    EvaluationSchedulerAudit,
    EvaluationSchedulerConfiguration,
    EvaluationSchedulerEvent,
    EvaluationSchedulerEventType,
)

Sleeper = Callable[[float], Awaitable[None]]
Monotonic = Callable[[], float]
EvaluateAttempt = Callable[[], Awaitable[EvaluationCaseResult]]

RETRYABLE_INFRASTRUCTURE_CODES = frozenset(
    {
        "RATE_LIMITED",
        "TIMEOUT",
        "CONNECTION_FAILED",
        "UPSTREAM_SERVER_ERROR",
    }
)


@dataclass(slots=True)
class EvaluationScheduler:
    """Serialize calls, pace them, and retry only infrastructure failures."""

    configuration: EvaluationSchedulerConfiguration
    sleeper: Sleeper = asyncio.sleep
    monotonic: Monotonic = time.monotonic
    random_source: random.Random = field(default_factory=random.Random)
    _last_request_started: float | None = field(default=None, init=False)
    _consecutive_rate_limits: int = field(default=0, init=False)
    _circuit_pauses: int = field(default=0, init=False)
    _upstream_attempts: int = field(default=0, init=False)
    _events: list[EvaluationSchedulerEvent] = field(default_factory=list, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    async def evaluate(
        self,
        *,
        case_id: str,
        repeat_index: int,
        attempt: EvaluateAttempt,
    ) -> EvaluationCaseResult:
        async with self._lock:
            latest: EvaluationCaseResult | None = None
            for provider_attempt in range(1, self.configuration.maximum_provider_attempts + 1):
                await self._pace(
                    case_id=case_id,
                    repeat_index=repeat_index,
                    provider_attempt=provider_attempt,
                )
                self._last_request_started = self.monotonic()
                latest = await attempt()
                self._upstream_attempts += latest.attempt_count
                if not self._retryable(latest):
                    self._consecutive_rate_limits = 0
                    return latest

                if latest.provider_error_code == "RATE_LIMITED":
                    self._consecutive_rate_limits += 1
                else:
                    self._consecutive_rate_limits = 0

                if provider_attempt >= self.configuration.maximum_provider_attempts:
                    return latest

                if (
                    self._consecutive_rate_limits
                    >= self.configuration.consecutive_rate_limit_threshold
                    and self._circuit_pauses < self.configuration.maximum_circuit_pauses
                ):
                    self._circuit_pauses += 1
                    await self._wait(
                        case_id=case_id,
                        repeat_index=repeat_index,
                        provider_attempt=provider_attempt,
                        event_type=EvaluationSchedulerEventType.CIRCUIT_PAUSE,
                        seconds=self.configuration.circuit_pause_seconds,
                        reason="consecutive_rate_limit_threshold",
                    )
                    self._consecutive_rate_limits = 0

                delay = self._retry_delay(latest, provider_attempt)
                await self._wait(
                    case_id=case_id,
                    repeat_index=repeat_index,
                    provider_attempt=provider_attempt,
                    event_type=EvaluationSchedulerEventType.RETRY_BACKOFF,
                    seconds=delay,
                    reason=latest.provider_error_code or "infrastructure_failure",
                )
            assert latest is not None
            return latest

    def audit(self) -> EvaluationSchedulerAudit:
        return EvaluationSchedulerAudit(
            events=tuple(self._events),
            total_wait_seconds=sum(event.wait_seconds for event in self._events),
            retry_count=sum(
                event.event_type is EvaluationSchedulerEventType.RETRY_BACKOFF
                for event in self._events
            ),
            rate_limit_count=sum(
                event.event_type is EvaluationSchedulerEventType.RETRY_BACKOFF
                and event.reason == "RATE_LIMITED"
                for event in self._events
            ),
            circuit_pause_count=self._circuit_pauses,
            upstream_attempt_count=self._upstream_attempts,
        )

    async def _pace(
        self,
        *,
        case_id: str,
        repeat_index: int,
        provider_attempt: int,
    ) -> None:
        if self._last_request_started is None:
            return
        adaptive_interval = self.configuration.minimum_request_interval_seconds * (
            2**self._circuit_pauses
        )
        elapsed = max(0.0, self.monotonic() - self._last_request_started)
        wait = max(0.0, adaptive_interval - elapsed)
        if wait:
            await self._wait(
                case_id=case_id,
                repeat_index=repeat_index,
                provider_attempt=provider_attempt,
                event_type=EvaluationSchedulerEventType.PACE_WAIT,
                seconds=wait,
                reason="minimum_request_interval",
            )

    def _retry_delay(
        self,
        result: EvaluationCaseResult,
        provider_attempt: int,
    ) -> float:
        trusted_retry_after = result.provider_retry_after_seconds
        configured_retry_after = self.configuration.retry_after_seconds
        if trusted_retry_after is not None:
            base = trusted_retry_after
        elif configured_retry_after is not None:
            base = configured_retry_after
        else:
            base = min(
                self.configuration.maximum_backoff_seconds,
                self.configuration.initial_backoff_seconds * (2 ** (provider_attempt - 1)),
            )
        jitter = self.random_source.uniform(0, self.configuration.jitter_seconds)
        return min(self.configuration.maximum_backoff_seconds, base + jitter)

    async def _wait(
        self,
        *,
        case_id: str,
        repeat_index: int,
        provider_attempt: int,
        event_type: EvaluationSchedulerEventType,
        seconds: float,
        reason: str,
    ) -> None:
        self._events.append(
            EvaluationSchedulerEvent(
                sequence_no=len(self._events) + 1,
                event_type=event_type,
                case_id=case_id,
                repeat_index=repeat_index,
                provider_attempt=provider_attempt,
                wait_seconds=seconds,
                reason=reason,
            )
        )
        await self.sleeper(seconds)

    @staticmethod
    def _retryable(result: EvaluationCaseResult) -> bool:
        return (
            result.status is EvaluationCaseStatus.PROVIDER_FAILED
            and result.provider_error_code in RETRYABLE_INFRASTRUCTURE_CODES
        )

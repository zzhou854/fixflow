"""Small process-local circuit breakers isolated by provider and model."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.llm.errors import LLMProviderError, LLMProviderErrorCode


class CircuitState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


@dataclass(frozen=True, slots=True)
class CircuitSnapshot:
    state: CircuitState
    consecutive_failures: int
    opened_at: float | None


class CircuitBreakerPort(Protocol):
    async def before_call(self) -> None: ...

    async def record_success(self) -> None: ...

    async def record_transport_failure(self) -> None: ...

    async def record_non_transport_failure(self) -> None: ...

    def snapshot(self) -> CircuitSnapshot: ...


class CircuitBreaker:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        failure_threshold: int = 2,
        recovery_seconds: float = 30,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1 or recovery_seconds <= 0:
            raise ValueError("invalid circuit-breaker configuration")
        self.provider = provider
        self.model = model
        self._threshold = failure_threshold
        self._recovery = recovery_seconds
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_call_active = False
        self._lock = asyncio.Lock()

    async def before_call(self) -> None:
        async with self._lock:
            if self._state is CircuitState.OPEN:
                assert self._opened_at is not None
                if self._clock() - self._opened_at >= self._recovery:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_call_active = False
                else:
                    self._raise_open()
            if self._state is CircuitState.HALF_OPEN:
                if self._half_open_call_active:
                    self._raise_open()
                self._half_open_call_active = True

    async def record_success(self) -> None:
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = None
            self._half_open_call_active = False

    async def record_transport_failure(self) -> None:
        async with self._lock:
            self._half_open_call_active = False
            self._failures += 1
            if self._state is CircuitState.HALF_OPEN or self._failures >= self._threshold:
                self._state = CircuitState.OPEN
                self._opened_at = self._clock()

    async def record_non_transport_failure(self) -> None:
        async with self._lock:
            self._half_open_call_active = False

    def snapshot(self) -> CircuitSnapshot:
        return CircuitSnapshot(self._state, self._failures, self._opened_at)

    def _raise_open(self) -> None:
        raise LLMProviderError(
            LLMProviderErrorCode.CIRCUIT_OPEN,
            provider=self.provider,
            model=self.model,
            retryable=False,
            safe_detail="provider circuit is open",
        )


class CircuitBreakerRegistry:
    """Process-local registry; each API process has independent breaker state."""

    def __init__(self) -> None:
        self._breakers: dict[tuple[str, str], CircuitBreaker] = {}

    def get(
        self,
        provider: str,
        model: str,
        *,
        failure_threshold: int = 2,
        recovery_seconds: float = 30,
    ) -> CircuitBreaker:
        key = (provider, model)
        if key not in self._breakers:
            self._breakers[key] = CircuitBreaker(
                provider=provider,
                model=model,
                failure_threshold=failure_threshold,
                recovery_seconds=recovery_seconds,
            )
        return self._breakers[key]

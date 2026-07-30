"""One monotonic deadline shared by every model call in one product request."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.llm.errors import LLMProviderError, LLMProviderErrorCode

Clock = Callable[[], float]
_budget: ContextVar[ModelCallBudget | None] = ContextVar("model_call_budget", default=None)


class ModelCallBudget:
    def __init__(self, total_seconds: float = 25.0, *, clock: Clock = time.monotonic) -> None:
        if not 0 < total_seconds <= 25:
            raise ValueError("model call budget must be in (0, 25] seconds")
        self.total_seconds = total_seconds
        self._clock = clock
        self._started_at = clock()

    @property
    def elapsed_seconds(self) -> float:
        return max(0.0, self._clock() - self._started_at)

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.total_seconds - self.elapsed_seconds)

    def timeout_seconds(
        self,
        *,
        provider: str,
        model: str,
        requested_seconds: float | None = None,
    ) -> float:
        remaining = self.remaining_seconds
        if remaining <= 0:
            raise LLMProviderError(
                LLMProviderErrorCode.BUDGET_EXHAUSTED,
                provider=provider,
                model=model,
                retryable=False,
                safe_detail="model time budget exhausted",
            )
        return remaining if requested_seconds is None else min(remaining, requested_seconds)


def current_model_call_budget() -> ModelCallBudget | None:
    return _budget.get()


@contextmanager
def bind_model_call_budget(budget: ModelCallBudget) -> Iterator[ModelCallBudget]:
    token = _budget.set(budget)
    try:
        yield budget
    finally:
        _budget.reset(token)

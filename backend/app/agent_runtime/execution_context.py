"""Trusted per-run correlation propagated across async graph and MCP calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from app.replay.capture import ReplayCapturePort
    from app.trace.runtime import TraceRuntime


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    run_id: UUID
    thread_id: UUID
    trace_id: UUID
    trace: TraceRuntime | None
    replay_capture: ReplayCapturePort | None = None


_CURRENT: ContextVar[ExecutionContext | None] = ContextVar(
    "fixflow_execution_context", default=None
)


def current_execution_context() -> ExecutionContext | None:
    return _CURRENT.get()


@contextmanager
def bind_execution_context(
    run_id: UUID,
    thread_id: UUID,
    trace_id: UUID,
    trace: TraceRuntime | None,
    replay_capture: ReplayCapturePort | None = None,
) -> Iterator[None]:
    token = _CURRENT.set(
        ExecutionContext(
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            trace=trace,
            replay_capture=replay_capture,
        )
    )
    try:
        yield
    finally:
        _CURRENT.reset(token)

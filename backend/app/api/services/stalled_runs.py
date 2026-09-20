"""Lifespan-owned reconciliation for Agent runs left without a terminal result."""

from __future__ import annotations

import asyncio
from contextlib import suppress

import structlog

from app.application.agent_reliability import AgentReliabilityService


class StalledAgentRunMonitor:
    def __init__(
        self,
        service: AgentReliabilityService,
        *,
        scan_interval_seconds: float = 30,
        stalled_after_seconds: int = 120,
    ) -> None:
        self._service = service
        self._scan_interval = scan_interval_seconds
        self._stalled_after = stalled_after_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._logger = structlog.get_logger("fixflow.stalled_agent_runs")

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="stalled-agent-run-monitor")

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                count = await self._service.reconcile_stalled_runs(
                    older_than_seconds=self._stalled_after
                )
                if count:
                    self._logger.warning("stalled_agent_runs_reconciled", count=count)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("stalled_agent_run_reconciliation_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._scan_interval)
            except TimeoutError:
                continue

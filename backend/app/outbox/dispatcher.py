"""Non-blocking at-least-once dispatcher; consumers run outside claim transactions."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from app.outbox.consumer import TraceDomainEventProjector
from app.outbox.repository import SqlAlchemyOutboxDispatchRepository


class OutboxDispatcher:
    def __init__(
        self,
        repository: SqlAlchemyOutboxDispatchRepository,
        consumer: TraceDomainEventProjector,
        *,
        worker_id: str,
        lease_seconds: int,
        batch_size: int,
        max_attempts: int,
        retry_base_seconds: float,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._consumer = consumer
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._retry_base_seconds = retry_base_seconds
        self._clock = clock

    async def dispatch_once(self) -> int:
        events = await self._repository.claim_batch(
            worker_id=self._worker_id,
            now=self._clock(),
            lease_seconds=self._lease_seconds,
            batch_size=self._batch_size,
        )
        for event in events:
            try:
                await self._consumer.consume(event)
            except Exception as exc:
                await self._repository.mark_failed(
                    event.event_id,
                    worker_id=self._worker_id,
                    claim_token=event.claim_token,
                    occurred_at=self._clock(),
                    error_code=type(exc).__name__,
                    error_message="consumer delivery failed",
                    max_attempts=self._max_attempts,
                    retry_base_seconds=self._retry_base_seconds,
                )
            else:
                await self._repository.mark_dispatched(
                    event.event_id,
                    worker_id=self._worker_id,
                    claim_token=event.claim_token,
                    occurred_at=self._clock(),
                )
        return len(events)

    async def run_forever(self, poll_interval_seconds: float) -> None:
        while True:
            processed = await self.dispatch_once()
            if processed == 0:
                await asyncio.sleep(poll_interval_seconds)

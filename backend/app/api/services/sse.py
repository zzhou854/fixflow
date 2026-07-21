"""Bounded in-memory SSE fan-out for the single-process demonstration runtime."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.api.schemas.agent import SSEEvent


class SSEEventBus:
    TERMINAL_EVENT_TYPES = frozenset({"assistant_completed", "interrupt_required", "run_failed"})

    def __init__(self, *, queue_size: int = 32) -> None:
        if queue_size <= 0:
            raise ValueError("queue_size must be positive")
        self._queue_size = queue_size
        self._subscribers: dict[UUID, set[asyncio.Queue[SSEEvent]]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._closed = False

    async def publish(
        self, thread_id: UUID, trace_id: UUID, event_type: str, data: dict[str, object]
    ) -> SSEEvent:
        event = SSEEvent(
            event_id=uuid4(),
            event_type=event_type,
            thread_id=thread_id,
            trace_id=trace_id,
            timestamp=datetime.now(UTC),
            data=data,
        )
        async with self._lock:
            queues = tuple(self._subscribers.get(thread_id, ()))
        for queue in queues:
            self._offer(queue, event)
        return event

    @classmethod
    def _offer(cls, queue: asyncio.Queue[SSEEvent], event: SSEEvent) -> None:
        if not queue.full():
            queue.put_nowait(event)
            return
        buffered: list[SSEEvent] = []
        while not queue.empty():
            buffered.append(queue.get_nowait())
        if event.event_type == "workflow_updated":
            replace = next(
                (
                    index
                    for index, item in enumerate(buffered)
                    if item.event_type == event.event_type
                ),
                None,
            )
        else:
            replace = next(
                (
                    index
                    for index, item in enumerate(buffered)
                    if item.event_type not in cls.TERMINAL_EVENT_TYPES
                ),
                None,
            )
        if replace is None:
            # Existing terminal events are more valuable than a late non-terminal event.
            if event.event_type not in cls.TERMINAL_EVENT_TYPES:
                for item in buffered:
                    queue.put_nowait(item)
                return
            replace = 0
        buffered.pop(replace)
        buffered.append(event)
        for item in buffered:
            queue.put_nowait(item)

    @asynccontextmanager
    async def subscribe(self, thread_id: UUID) -> AsyncIterator[asyncio.Queue[SSEEvent]]:
        if self._closed:
            raise RuntimeError("SSE event bus is closed")
        queue: asyncio.Queue[SSEEvent] = asyncio.Queue(maxsize=self._queue_size)
        async with self._lock:
            self._subscribers[thread_id].add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(thread_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(thread_id, None)

    async def subscriber_count(self, thread_id: UUID) -> int:
        async with self._lock:
            return len(self._subscribers.get(thread_id, ()))

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            self._subscribers.clear()

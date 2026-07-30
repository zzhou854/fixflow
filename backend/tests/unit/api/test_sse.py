from uuid import uuid4

import pytest
from app.api.services.sse import SSEEventBus


@pytest.mark.asyncio
async def test_sse_bus_delivers_typed_event_and_cleans_subscriber() -> None:
    bus = SSEEventBus(queue_size=2)
    thread_id, trace_id = uuid4(), uuid4()
    async with bus.subscribe(thread_id) as queue:
        await bus.publish(thread_id, trace_id, "run_started", {})
        event = await queue.get()
        assert event.event_type == "run_started"
        assert await bus.subscriber_count(thread_id) == 1
    assert await bus.subscriber_count(thread_id) == 0


@pytest.mark.asyncio
async def test_slow_consumer_queue_remains_bounded() -> None:
    bus = SSEEventBus(queue_size=2)
    thread_id, trace_id = uuid4(), uuid4()
    async with bus.subscribe(thread_id) as queue:
        for index in range(10):
            await bus.publish(thread_id, trace_id, "assistant_delta", {"index": index})
        assert queue.qsize() == 2
        assert (await queue.get()).data["index"] == 8


@pytest.mark.asyncio
async def test_terminal_event_evicts_non_terminal_and_is_not_lost_to_late_delta() -> None:
    bus = SSEEventBus(queue_size=2)
    thread_id, trace_id = uuid4(), uuid4()
    async with bus.subscribe(thread_id) as queue:
        await bus.publish(thread_id, trace_id, "workflow_updated", {"version": 1})
        await bus.publish(thread_id, trace_id, "assistant_delta", {"text": "partial"})
        await bus.publish(thread_id, trace_id, "message.completed", {"text": "done"})
        await bus.publish(thread_id, trace_id, "assistant_delta", {"text": "late"})
        events = [await queue.get(), await queue.get()]
    assert [item.event_type for item in events] == ["message.completed", "assistant_delta"]


@pytest.mark.asyncio
async def test_latest_workflow_update_replaces_stale_update() -> None:
    bus = SSEEventBus(queue_size=2)
    thread_id, trace_id = uuid4(), uuid4()
    async with bus.subscribe(thread_id) as queue:
        await bus.publish(thread_id, trace_id, "workflow_updated", {"version": 1})
        await bus.publish(thread_id, trace_id, "assistant_delta", {"text": "partial"})
        await bus.publish(thread_id, trace_id, "workflow_updated", {"version": 2})
        events = [await queue.get(), await queue.get()]
    assert events[-1].event_type == "workflow_updated"
    assert events[-1].data == {"version": 2}


@pytest.mark.asyncio
async def test_close_clears_subscriber_registry() -> None:
    bus = SSEEventBus()
    thread_id = uuid4()
    context = bus.subscribe(thread_id)
    await context.__aenter__()
    assert await bus.subscriber_count(thread_id) == 1
    await bus.close()
    assert await bus.subscriber_count(thread_id) == 0
    await context.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_duplicate_public_terminal_for_same_run_is_not_redelivered() -> None:
    bus = SSEEventBus()
    thread_id, trace_id, run_id = uuid4(), uuid4(), uuid4()
    async with bus.subscribe(thread_id) as queue:
        await bus.publish(
            thread_id,
            trace_id,
            "message.completed",
            {"text": "done"},
            run_id=run_id,
        )
        await bus.publish(
            thread_id,
            trace_id,
            "message.failed",
            {"text": "late"},
            run_id=run_id,
        )
        events = []
        while not queue.empty():
            events.append(await queue.get())
    assert [item.event_type for item in events] == ["message.completed"]

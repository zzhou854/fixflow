import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.infrastructure.database.models.observability import (
    AgentRunStatus,
    AgentRunTrigger,
    AgentTraceEvent,
    TraceSource,
)
from app.trace.models import StartRun, TracePayload
from app.trace.runtime import TraceConflict, TraceRuntime
from app.trace.sanitizer import TraceSanitizer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_run_lifecycle_allocates_gap_free_sequences_under_concurrency(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    runtime = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))
    run_id, thread_id, trace_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    await runtime.start_run(
        StartRun(
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            trigger=AgentRunTrigger.MESSAGE,
            actor_type="RESIDENT",
            actor_id=uuid4(),
            started_at=now,
        )
    )
    await asyncio.gather(
        *(
            runtime.append_event(
                event_key=runtime.event_key(run_id, "node_completed", str(index)),
                run_id=run_id,
                thread_id=thread_id,
                trace_id=trace_id,
                source=TraceSource.AGENT,
                event_type="node_completed",
                node_name=f"node_{index}",
                payload=TracePayload(node_name=f"node_{index}"),
                occurred_at=now,
            )
            for index in range(10)
        )
    )
    await runtime.finish_run(run_id, status=AgentRunStatus.COMPLETED, occurred_at=now)
    events = await runtime.list_events(run_id)
    assert [event.sequence_number for event in events] == list(range(1, 13))
    assert (await runtime.get_run(run_id)).status is AgentRunStatus.COMPLETED  # type: ignore[union-attr]
    await engine.dispose()


@pytest.mark.asyncio
async def test_trace_event_key_and_terminal_transition_are_idempotent(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    runtime = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))
    run_id, trace_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    await runtime.start_run(
        StartRun(
            run_id=run_id,
            thread_id=uuid4(),
            trace_id=trace_id,
            trigger=AgentRunTrigger.RESUME,
            actor_type="RESIDENT",
            actor_id=uuid4(),
            started_at=now,
        )
    )
    key = runtime.event_key(run_id, "resume_accepted")
    first = await runtime.append_event(
        event_key=key,
        run_id=run_id,
        thread_id=None,
        trace_id=trace_id,
        source=TraceSource.AGENT,
        event_type="resume_accepted",
        payload=TracePayload(summary="accepted"),
        occurred_at=now,
    )
    replay = await runtime.append_event(
        event_key=key,
        run_id=run_id,
        thread_id=None,
        trace_id=trace_id,
        source=TraceSource.AGENT,
        event_type="resume_accepted",
        payload=TracePayload(summary="accepted"),
        occurred_at=now,
    )
    assert replay.event_id == first.event_id
    await runtime.finish_run(run_id, status=AgentRunStatus.INTERRUPTED, occurred_at=now)
    await runtime.finish_run(run_id, status=AgentRunStatus.INTERRUPTED, occurred_at=now)
    with pytest.raises(TraceConflict):
        await runtime.finish_run(run_id, status=AgentRunStatus.COMPLETED, occurred_at=now)
    await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_run_accepts_late_domain_audit_but_rejects_control_events(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    runtime = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))
    run_id, trace_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    await runtime.start_run(
        StartRun(
            run_id=run_id,
            thread_id=uuid4(),
            trace_id=trace_id,
            trigger=AgentRunTrigger.MESSAGE,
            actor_type="RESIDENT",
            actor_id=uuid4(),
            started_at=now,
        )
    )
    await runtime.finish_run(run_id, status=AgentRunStatus.COMPLETED, occurred_at=now)
    domain = await runtime.append_event(
        event_key=runtime.event_key(run_id, "domain_ticket_created"),
        run_id=run_id,
        thread_id=None,
        trace_id=trace_id,
        source=TraceSource.DOMAIN,
        event_type="domain_ticket_created",
        payload=TracePayload(aggregate_type="repair_ticket", aggregate_id=uuid4()),
        occurred_at=now,
    )
    assert domain.sequence_number == 3
    assert (await runtime.get_run(run_id)).status is AgentRunStatus.COMPLETED  # type: ignore[union-attr]
    with pytest.raises(TraceConflict, match="control-plane"):
        await runtime.append_event(
            event_key=runtime.event_key(run_id, "node_started"),
            run_id=run_id,
            thread_id=None,
            trace_id=trace_id,
            source=TraceSource.AGENT,
            event_type="node_started",
            payload=TracePayload(node_name="late"),
            occurred_at=now,
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_terminal_transitions_allow_one_lifecycle_event(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    runtime = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))
    run_id, trace_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    await runtime.start_run(
        StartRun(
            run_id=run_id,
            thread_id=uuid4(),
            trace_id=trace_id,
            trigger=AgentRunTrigger.MESSAGE,
            actor_type="RESIDENT",
            actor_id=uuid4(),
            started_at=now,
        )
    )
    results = await asyncio.gather(
        runtime.finish_run(run_id, status=AgentRunStatus.COMPLETED, occurred_at=now),
        runtime.finish_run(run_id, status=AgentRunStatus.FAILED, occurred_at=now),
        return_exceptions=True,
    )
    assert sum(isinstance(result, TraceConflict) for result in results) == 1
    async with sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(AgentTraceEvent)
            .where(
                AgentTraceEvent.event_type.in_(
                    ("run_interrupted", "run_completed", "run_failed_safe", "run_failed")
                ),
                AgentTraceEvent.run_id == run_id,
            )
        )
        assert count == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_terminal_persistence_failure_rolls_back_run_status_and_sequence(
    migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    runtime = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))
    run_id, trace_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    await runtime.start_run(
        StartRun(
            run_id=run_id,
            thread_id=uuid4(),
            trace_id=trace_id,
            trigger=AgentRunTrigger.MESSAGE,
            actor_type="RESIDENT",
            actor_id=uuid4(),
            started_at=now,
        )
    )

    async def fail_flush(self: object, *args: object, **kwargs: object) -> None:
        del self, args, kwargs
        raise RuntimeError("terminal persistence failed")

    with monkeypatch.context() as patch:
        patch.setattr("sqlalchemy.ext.asyncio.AsyncSession.flush", fail_flush)
        with pytest.raises(RuntimeError, match="terminal persistence"):
            await runtime.finish_run(run_id, status=AgentRunStatus.COMPLETED, occurred_at=now)
    restored = await runtime.get_run(run_id)
    assert restored is not None
    assert restored.status is AgentRunStatus.RUNNING
    events = await runtime.list_events(run_id)
    assert [event.sequence_number for event in events] == [1]
    await engine.dispose()


@pytest.mark.asyncio
async def test_runless_event_has_no_sequence_and_never_changes_a_run(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    runtime = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))
    event = await runtime.append_event(
        event_key=uuid4().hex,
        run_id=None,
        thread_id=uuid4(),
        trace_id=uuid4(),
        source=TraceSource.API,
        event_type="api_request_conflict",
        payload=TracePayload(idempotency_key_fingerprint="a" * 64),
        occurred_at=datetime.now(UTC),
    )
    assert event.run_id is None
    assert event.sequence_number is None
    await engine.dispose()

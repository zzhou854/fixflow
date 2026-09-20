import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.infrastructure.database.models.observability import (
    AgentTraceEvent,
    OutboxEvent,
    OutboxStatus,
)
from app.outbox.consumer import TraceDomainEventProjector
from app.outbox.dispatcher import OutboxDispatcher
from app.outbox.models import ClaimedOutboxEvent
from app.outbox.repository import OutboxLeaseLost, SqlAlchemyOutboxDispatchRepository
from app.trace.runtime import TraceRuntime
from app.trace.sanitizer import TraceSanitizer
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


@pytest.mark.asyncio
async def test_dispatcher_projects_once_and_marks_dispatched(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        await session.execute(delete(OutboxEvent))
    row = OutboxEvent(
        id=uuid4(),
        event_key=uuid4().hex + uuid4().hex,
        event_type="ticket.created",
        aggregate_type="repair_ticket",
        aggregate_id=uuid4(),
        aggregate_version=1,
        operation_id=uuid4(),
        trace_id=uuid4(),
        run_id=None,
        thread_id=None,
        actor_type="RESIDENT",
        actor_id=uuid4(),
        payload={"status": "OPEN", "issue_category": "WATER_LEAK"},
        status=OutboxStatus.PENDING,
        occurred_at=now,
        available_at=now,
        attempt_count=0,
    )
    async with sessions.begin() as session:
        session.add(row)
    trace = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))
    repository = SqlAlchemyOutboxDispatchRepository(sessions)
    projector = TraceDomainEventProjector(trace)
    dispatcher = OutboxDispatcher(
        repository,
        projector,
        worker_id="worker-a",
        lease_seconds=30,
        batch_size=10,
        max_attempts=3,
        retry_base_seconds=1,
        clock=lambda: now,
    )
    assert await dispatcher.dispatch_once() == 1
    assert await dispatcher.dispatch_once() == 0
    claimed = await repository.claim_batch(
        worker_id="response-loss",
        now=now,
        lease_seconds=30,
        batch_size=10,
    )
    assert claimed == ()
    # A lost consumer acknowledgement may replay the same payload directly;
    # the outbox-derived event key keeps the Trace projection single-row.
    replay = ClaimedOutboxEvent(
        event_id=row.id,
        claim_token=uuid4(),
        event_key=row.event_key,
        event_type=row.event_type,
        aggregate_type=row.aggregate_type,
        aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,
        operation_id=row.operation_id,
        trace_id=row.trace_id,
        run_id=row.run_id,
        thread_id=row.thread_id,
        actor_type=row.actor_type,
        actor_id=row.actor_id,
        payload=row.payload,
        attempt_count=row.attempt_count,
        occurred_at=row.occurred_at,
        available_at=row.available_at,
        created_at=row.created_at,
    )
    await projector.consume(replay)
    async with sessions() as session:
        stored = await session.get(OutboxEvent, row.id)
        assert stored is not None and stored.status is OutboxStatus.DISPATCHED
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentTraceEvent)
                .where(AgentTraceEvent.event_key == f"outbox:{row.event_key}")
            )
            == 1
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed_and_failures_back_off(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        await session.execute(delete(OutboxEvent))
    row = OutboxEvent(
        id=uuid4(),
        event_key=uuid4().hex + uuid4().hex,
        event_type="unsupported.event",
        aggregate_type="repair_ticket",
        aggregate_id=uuid4(),
        aggregate_version=1,
        operation_id=uuid4(),
        trace_id=uuid4(),
        actor_type="SYSTEM",
        actor_id=uuid4(),
        payload={},
        status=OutboxStatus.PROCESSING,
        occurred_at=now - timedelta(minutes=1),
        available_at=now - timedelta(minutes=1),
        claimed_by="dead-worker",
        claim_token=uuid4(),
        claim_expires_at=now - timedelta(seconds=1),
        attempt_count=0,
    )
    async with sessions.begin() as session:
        session.add(row)
    trace = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))
    dispatcher = OutboxDispatcher(
        SqlAlchemyOutboxDispatchRepository(sessions),
        TraceDomainEventProjector(trace),
        worker_id="worker-b",
        lease_seconds=30,
        batch_size=1,
        max_attempts=2,
        retry_base_seconds=5,
        clock=lambda: now,
    )
    assert await dispatcher.dispatch_once() == 1
    async with sessions() as session:
        stored = await session.scalar(select(OutboxEvent).where(OutboxEvent.id == row.id))
        assert stored is not None
        assert stored.status is OutboxStatus.PENDING
        assert stored.attempt_count == 1
        assert stored.available_at == now + timedelta(seconds=5)
    later = now + timedelta(seconds=5)
    dead_letter_dispatcher = OutboxDispatcher(
        SqlAlchemyOutboxDispatchRepository(sessions),
        TraceDomainEventProjector(trace),
        worker_id="worker-c",
        lease_seconds=30,
        batch_size=1,
        max_attempts=2,
        retry_base_seconds=5,
        clock=lambda: later,
    )
    assert await dead_letter_dispatcher.dispatch_once() == 1
    async with sessions() as session:
        stored = await session.get(OutboxEvent, row.id)
        assert stored is not None and stored.status is OutboxStatus.DEAD_LETTER
        assert stored.attempt_count == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_claim_token_fences_a_stale_worker_even_when_worker_id_is_reused(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    row = OutboxEvent(
        id=uuid4(),
        event_key=uuid4().hex + uuid4().hex,
        event_type="ticket.created",
        aggregate_type="repair_ticket",
        aggregate_id=uuid4(),
        aggregate_version=1,
        operation_id=uuid4(),
        trace_id=uuid4(),
        actor_type="SYSTEM",
        actor_id=uuid4(),
        payload={"status": "OPEN"},
        status=OutboxStatus.PENDING,
        occurred_at=now,
        available_at=now,
        attempt_count=0,
    )
    async with sessions.begin() as session:
        await session.execute(delete(OutboxEvent))
        session.add(row)

    repository = SqlAlchemyOutboxDispatchRepository(sessions)
    (first_claim,) = await repository.claim_batch(
        worker_id="reused-worker", now=now, lease_seconds=1, batch_size=1
    )
    (second_claim,) = await repository.claim_batch(
        worker_id="reused-worker",
        now=now + timedelta(seconds=2),
        lease_seconds=30,
        batch_size=1,
    )
    assert first_claim.claim_token != second_claim.claim_token

    with pytest.raises(OutboxLeaseLost, match="STALE_OUTBOX_CLAIM"):
        await repository.mark_dispatched(
            row.id,
            worker_id="reused-worker",
            claim_token=first_claim.claim_token,
            occurred_at=now + timedelta(seconds=2),
        )
    async with sessions() as session:
        stored = await session.get(OutboxEvent, row.id)
        assert stored is not None
        assert stored.status is OutboxStatus.PROCESSING
        assert stored.claim_token == second_claim.claim_token

    await repository.mark_dispatched(
        row.id,
        worker_id="reused-worker",
        claim_token=second_claim.claim_token,
        occurred_at=now + timedelta(seconds=2),
    )
    with pytest.raises(OutboxLeaseLost, match="STALE_OUTBOX_CLAIM"):
        await repository.mark_failed(
            row.id,
            worker_id="reused-worker",
            claim_token=first_claim.claim_token,
            occurred_at=now + timedelta(seconds=3),
            error_code="EXPIRED",
            error_message="stale worker must not overwrite dispatch",
            max_attempts=3,
            retry_base_seconds=1,
        )
    async with sessions() as session:
        stored = await session.get(OutboxEvent, row.id)
        assert stored is not None
        assert stored.status is OutboxStatus.DISPATCHED
        assert stored.attempt_count == 0
        assert stored.last_error_code is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_stale_ack_after_consumer_commit_replays_idempotently_to_one_trace_event(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    row = OutboxEvent(
        id=uuid4(),
        event_key=uuid4().hex + uuid4().hex,
        event_type="ticket.created",
        aggregate_type="repair_ticket",
        aggregate_id=uuid4(),
        aggregate_version=1,
        operation_id=uuid4(),
        trace_id=uuid4(),
        actor_type="SYSTEM",
        actor_id=uuid4(),
        payload={"status": "OPEN"},
        status=OutboxStatus.PENDING,
        occurred_at=now,
        available_at=now,
        attempt_count=0,
    )
    async with sessions.begin() as session:
        await session.execute(delete(OutboxEvent))
        session.add(row)
    repository = SqlAlchemyOutboxDispatchRepository(sessions)
    trace = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))
    projector = TraceDomainEventProjector(trace)
    (first_claim,) = await repository.claim_batch(
        worker_id="worker-a", now=now, lease_seconds=1, batch_size=1
    )
    await projector.consume(first_claim)
    (second_claim,) = await repository.claim_batch(
        worker_id="worker-b",
        now=now + timedelta(seconds=2),
        lease_seconds=30,
        batch_size=1,
    )
    with pytest.raises(OutboxLeaseLost, match="STALE_OUTBOX_CLAIM"):
        await repository.mark_dispatched(
            row.id,
            worker_id="worker-a",
            claim_token=first_claim.claim_token,
            occurred_at=now + timedelta(seconds=2),
        )
    await projector.consume(second_claim)
    await repository.mark_dispatched(
        row.id,
        worker_id="worker-b",
        claim_token=second_claim.claim_token,
        occurred_at=now + timedelta(seconds=2),
    )
    async with sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AgentTraceEvent)
                .where(AgentTraceEvent.event_key == f"outbox:{row.event_key}")
            )
            == 1
        )
        stored = await session.get(OutboxEvent, row.id)
        assert stored is not None and stored.status is OutboxStatus.DISPATCHED
    await engine.dispose()


@pytest.mark.asyncio
async def test_two_dispatchers_do_not_double_claim_and_poison_event_does_not_block_batch(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sessions.begin() as session:
        await session.execute(delete(OutboxEvent))
        for index in range(12):
            session.add(
                OutboxEvent(
                    id=uuid4(),
                    event_key=uuid4().hex + uuid4().hex,
                    event_type="unsupported.event" if index == 0 else "ticket.created",
                    aggregate_type="repair_ticket",
                    aggregate_id=uuid4(),
                    aggregate_version=1,
                    operation_id=uuid4(),
                    trace_id=uuid4(),
                    actor_type="SYSTEM",
                    actor_id=uuid4(),
                    payload={"status": "OPEN"},
                    status=OutboxStatus.PENDING,
                    occurred_at=now,
                    available_at=now,
                    attempt_count=0,
                )
            )
    trace = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))

    def dispatcher(worker_id: str) -> OutboxDispatcher:
        return OutboxDispatcher(
            SqlAlchemyOutboxDispatchRepository(sessions),
            TraceDomainEventProjector(trace),
            worker_id=worker_id,
            lease_seconds=30,
            batch_size=10,
            max_attempts=1,
            retry_base_seconds=1,
            clock=lambda: now,
        )

    counts = await asyncio.gather(
        dispatcher("concurrent-a").dispatch_once(),
        dispatcher("concurrent-b").dispatch_once(),
    )
    assert sum(counts) == 12
    async with sessions() as session:
        statuses = list(await session.scalars(select(OutboxEvent.status)))
        assert statuses.count(OutboxStatus.DISPATCHED) == 11
        assert statuses.count(OutboxStatus.DEAD_LETTER) == 1
        domain_count = await session.scalar(
            select(func.count())
            .select_from(AgentTraceEvent)
            .where(AgentTraceEvent.source == "DOMAIN")
        )
        assert domain_count is not None and domain_count >= 11
    await engine.dispose()

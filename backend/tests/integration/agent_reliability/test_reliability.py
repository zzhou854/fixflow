"""PostgreSQL evidence for Agent message finalization and human review."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.application.agent_reliability import (
    AgentReliabilityConflict,
    AgentReliabilityService,
    AgentThreadPermissionDenied,
)
from app.application.agent_reliability_models import (
    FinalizeAgentRun,
    HumanReviewFailureStage,
    HumanReviewSafetyLevel,
    HumanReviewStatus,
    HumanReviewTransition,
    MessageOutcome,
    RequiredUserAction,
    ThreadLifecycleStatus,
)
from app.domain.enums import ActorType
from app.infrastructure.database.agent_reliability_uow import (
    SqlAlchemyAgentReliabilityUnitOfWork,
)
from app.infrastructure.database.models import Property, User
from app.infrastructure.database.models.agent_control import (
    AgentMessageRow,
    HumanReviewCaseEventRow,
    HumanReviewCaseRow,
)
from app.infrastructure.database.models.observability import (
    AgentRun,
    AgentRunStatus,
    AgentRunTrigger,
    AgentTraceEvent,
    OutboxEvent,
)
from app.trace.models import StartRun
from app.trace.runtime import TraceRuntime
from app.trace.sanitizer import TraceSanitizer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


async def _environment(
    database_url: str,
) -> tuple[
    AsyncEngine,
    async_sessionmaker[AsyncSession],
    AgentReliabilityService,
    TraceRuntime,
    UUID,
    UUID,
    UUID,
]:
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    resident_id, operator_id, property_id = uuid4(), uuid4(), uuid4()
    async with sessions() as session, session.begin():
        session.add_all(
            [
                User(
                    id=resident_id,
                    username=f"reliability-{resident_id}",
                    password_hash="test-only-hash",
                    role="RESIDENT",
                ),
                User(
                    id=operator_id,
                    username=f"operator-{operator_id}",
                    password_hash="test-only-hash",
                    role="OPERATOR",
                ),
                Property(
                    id=property_id,
                    community_name="可靠性测试小区",
                    building_no="1",
                    unit_no="1",
                    room_no=property_id.hex[:8],
                    address_text="测试地址",
                ),
            ]
        )
    service = AgentReliabilityService(lambda: SqlAlchemyAgentReliabilityUnitOfWork(sessions))
    trace = TraceRuntime(
        sessions,
        TraceSanitizer(max_payload_bytes=16_384, max_string_length=2_000),
    )
    return engine, sessions, service, trace, resident_id, operator_id, property_id


async def _start_run(
    trace: TraceRuntime,
    *,
    resident_id: UUID,
    property_id: UUID,
    thread_id: UUID,
    started_at: datetime | None = None,
) -> tuple[UUID, UUID]:
    run_id, trace_id = uuid4(), uuid4()
    await trace.start_run(
        StartRun(
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            trigger=AgentRunTrigger.MESSAGE,
            actor_type=ActorType.RESIDENT.value,
            actor_id=resident_id,
            user_id=resident_id,
            property_id=property_id,
            initial_intent_version=1,
            started_at=started_at or datetime.now(UTC),
        )
    )
    return run_id, trace_id


@pytest.mark.asyncio
async def test_finalize_message_is_atomic_idempotent_and_archivable(
    migrated_database_url: str,
) -> None:
    engine, sessions, service, trace, resident_id, _operator_id, property_id = await _environment(
        migrated_database_url
    )
    try:
        thread_id = uuid4()
        await service.register_thread(
            thread_id=thread_id, resident_id=resident_id, property_id=None
        )
        run_id, trace_id = await _start_run(
            trace,
            resident_id=resident_id,
            property_id=property_id,
            thread_id=thread_id,
        )
        command = FinalizeAgentRun(
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            resident_id=resident_id,
            property_id=property_id,
            intent_version=1,
            user_message_id=uuid4(),
            user_message="厨房漏水",
            user_message_created_at=datetime.now(UTC),
            assistant_message="已记录报修。",
            outcome=MessageOutcome.COMPLETED,
            required_user_action=RequiredUserAction.NONE,
            agent_run_status=AgentRunStatus.COMPLETED.value,
            agent_run_terminal_event_type="run_completed",
            message_event_type="message.completed",
            error_code=None,
        )
        assert await service.finalize_run(command) is None
        assert await service.finalize_run(command) is None

        messages = await service.list_messages(thread_id=thread_id, resident_id=resident_id)
        assert [item.content for item in messages] == ["厨房漏水", "已记录报修。"]
        async with sessions() as session:
            run = await session.get(AgentRun, run_id)
            assert run is not None
            assert run.message_outcome is MessageOutcome.COMPLETED
            events = tuple(
                await session.scalars(
                    select(AgentTraceEvent)
                    .where(AgentTraceEvent.run_id == run_id)
                    .order_by(AgentTraceEvent.sequence_number)
                )
            )
            assert [item.event_type for item in events][-2:] == [
                "run_completed",
                "message.completed",
            ]
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AgentMessageRow)
                    .where(AgentMessageRow.run_id == run_id)
                )
                == 2
            )

        record = (
            await service.list_threads(
                resident_id=resident_id,
                lifecycle_status=ThreadLifecycleStatus.ACTIVE,
                limit=5,
                offset=0,
            )
        )[0]
        archived = await service.set_thread_lifecycle(
            thread_id=thread_id,
            resident_id=resident_id,
            lifecycle_status=ThreadLifecycleStatus.ARCHIVED,
            actor_id=resident_id,
            expected_version=record.version,
        )
        assert archived.archived_at is not None
        assert not await service.list_threads(
            resident_id=resident_id,
            lifecycle_status=ThreadLifecycleStatus.ACTIVE,
            limit=5,
            offset=0,
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_escalation_creates_one_review_case_history_and_outbox(
    migrated_database_url: str,
) -> None:
    engine, sessions, service, trace, resident_id, operator_id, property_id = await _environment(
        migrated_database_url
    )
    try:
        thread_id = uuid4()
        await service.register_thread(
            thread_id=thread_id, resident_id=resident_id, property_id=property_id
        )
        cases = []
        for _ in range(2):
            run_id, trace_id = await _start_run(
                trace,
                resident_id=resident_id,
                property_id=property_id,
                thread_id=thread_id,
            )
            case = await service.finalize_run(
                FinalizeAgentRun(
                    run_id=run_id,
                    thread_id=thread_id,
                    trace_id=trace_id,
                    resident_id=resident_id,
                    property_id=property_id,
                    intent_version=1,
                    user_message_id=uuid4(),
                    user_message="插座冒烟",
                    user_message_created_at=datetime.now(UTC),
                    assistant_message="已转交物业紧急处理。",
                    outcome=MessageOutcome.ESCALATED,
                    required_user_action=RequiredUserAction.CONTACT_OPERATOR,
                    agent_run_status=AgentRunStatus.COMPLETED.value,
                    agent_run_terminal_event_type="run_completed",
                    message_event_type="message.escalated",
                    error_code="SAFETY_REVIEW_REQUIRED",
                    failure_stage=HumanReviewFailureStage.SAFETY_REVIEW,
                    reason_code="SAFETY_REVIEW_REQUIRED",
                    safety_level=HumanReviewSafetyLevel.EMERGENCY,
                )
            )
            assert case is not None
            cases.append(case)
        assert cases[0].case_id == cases[1].case_id

        transitioned = await service.transition_human_review(
            HumanReviewTransition(
                case_id=cases[0].case_id,
                actor_type=ActorType.OPERATOR,
                actor_id=operator_id,
                trace_id=uuid4(),
                expected_version=1,
                target_status=HumanReviewStatus.CLAIMED,
            )
        )
        assert transitioned.status is HumanReviewStatus.CLAIMED
        with pytest.raises(AgentReliabilityConflict):
            await service.transition_human_review(
                HumanReviewTransition(
                    case_id=transitioned.case_id,
                    actor_type=ActorType.OPERATOR,
                    actor_id=operator_id,
                    trace_id=uuid4(),
                    expected_version=1,
                    target_status=HumanReviewStatus.RESOLVED,
                    resolution_code="STALE",
                )
            )
        history = await service.list_human_review_events(
            actor_type=ActorType.OPERATOR,
            case_id=transitioned.case_id,
        )
        assert [item.action for item in history] == ["CREATED", "CLAIMED"]
        async with sessions() as session:
            assert await session.scalar(select(func.count()).select_from(HumanReviewCaseRow)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(HumanReviewCaseEventRow)) == 2
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.aggregate_type == "HUMAN_REVIEW_CASE")
                )
                == 2
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stalled_run_reconciliation_produces_failed_public_terminal(
    migrated_database_url: str,
) -> None:
    engine, sessions, service, trace, resident_id, _operator_id, property_id = await _environment(
        migrated_database_url
    )
    try:
        thread_id = uuid4()
        await service.register_thread(
            thread_id=thread_id, resident_id=resident_id, property_id=property_id
        )
        run_id, _ = await _start_run(
            trace,
            resident_id=resident_id,
            property_id=property_id,
            thread_id=thread_id,
            started_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        assert await service.reconcile_stalled_runs(older_than_seconds=120) == 1
        async with sessions() as session:
            run = await session.get(AgentRun, run_id)
            assert run is not None
            assert run.status is AgentRunStatus.FAILED
            assert run.message_outcome is MessageOutcome.FAILED
            event_types = set(
                await session.scalars(
                    select(AgentTraceEvent.event_type).where(AgentTraceEvent.run_id == run_id)
                )
            )
            assert {"run_failed", "message.failed"} <= event_types
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_escalation_reuses_one_open_case(
    migrated_database_url: str,
) -> None:
    engine, sessions, service, trace, resident_id, _operator_id, property_id = await _environment(
        migrated_database_url
    )
    try:
        thread_id = uuid4()
        await service.register_thread(
            thread_id=thread_id, resident_id=resident_id, property_id=property_id
        )
        commands = []
        for message in ("第一次失败", "并发重试"):
            run_id, trace_id = await _start_run(
                trace,
                resident_id=resident_id,
                property_id=property_id,
                thread_id=thread_id,
            )
            commands.append(
                FinalizeAgentRun(
                    run_id=run_id,
                    thread_id=thread_id,
                    trace_id=trace_id,
                    resident_id=resident_id,
                    property_id=property_id,
                    intent_version=7,
                    user_message_id=uuid4(),
                    user_message=message,
                    user_message_created_at=datetime.now(UTC),
                    assistant_message="已转交物业处理。",
                    outcome=MessageOutcome.ESCALATED,
                    required_user_action=RequiredUserAction.CONTACT_OPERATOR,
                    agent_run_status=AgentRunStatus.COMPLETED.value,
                    agent_run_terminal_event_type="run_completed",
                    message_event_type="message.escalated",
                    error_code="POLICY_REVIEW_REQUIRED",
                    failure_stage=HumanReviewFailureStage.POLICY_REVIEW,
                    reason_code="POLICY_REVIEW_REQUIRED",
                )
            )
        results = await asyncio.gather(*(service.finalize_run(item) for item in commands))
        assert results[0] is not None and results[1] is not None
        assert results[0].case_id == results[1].case_id
        async with sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(HumanReviewCaseRow)
                    .where(HumanReviewCaseRow.intent_version == 7)
                )
                == 1
            )
            terminal_runs = await session.scalar(
                select(func.count())
                .select_from(AgentRun)
                .where(
                    AgentRun.id.in_([item.run_id for item in commands]),
                    AgentRun.message_outcome == MessageOutcome.ESCALATED,
                )
            )
            assert terminal_runs == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_recent_threads_are_limited_sorted_and_owner_protected(
    migrated_database_url: str,
) -> None:
    engine, _sessions, service, _trace, resident_id, operator_id, _property_id = await _environment(
        migrated_database_url
    )
    try:
        thread_ids = []
        for _ in range(6):
            thread_id = uuid4()
            thread_ids.append(thread_id)
            await service.register_thread(
                thread_id=thread_id,
                resident_id=resident_id,
                property_id=None,
            )
        recent = await service.list_threads(
            resident_id=resident_id,
            lifecycle_status=ThreadLifecycleStatus.ACTIVE,
            limit=5,
            offset=0,
        )
        assert len(recent) == 5
        assert recent[0].thread_id == thread_ids[-1]
        assert thread_ids[0] not in {item.thread_id for item in recent}
        with pytest.raises(AgentThreadPermissionDenied):
            await service.set_thread_lifecycle(
                thread_id=thread_ids[-1],
                resident_id=operator_id,
                lifecycle_status=ThreadLifecycleStatus.ARCHIVED,
                actor_id=operator_id,
                expected_version=recent[0].version,
            )
    finally:
        await engine.dispose()

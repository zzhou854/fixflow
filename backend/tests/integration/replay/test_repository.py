import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.agent.enums import AgentIntent, PendingAction
from app.domain.enums import ActorType, WorkflowStage
from app.infrastructure.database.models.observability import (
    AgentRunTrigger,
    AgentTraceEvent,
)
from app.replay.enums import (
    RecoveryRecommendation,
    ReplayBundleStatus,
    ReplayExecutionStatus,
)
from app.replay.models import (
    MessageReplayInput,
    ReplayBundleView,
    ReplayComparisonResult,
    ReplayExecutionView,
    ReplayMessageMetadata,
    ReplaySafeAgentState,
)
from app.replay.repository import (
    ReplayConflict,
    ReplayIdempotencyConflict,
    ReplayRepository,
)
from app.replay.steps import NodeEnteredStep
from app.trace.models import StartRun
from app.trace.runtime import TraceRuntime
from app.trace.sanitizer import TraceSanitizer
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _safe(*, thread_id: UUID, trace_id: UUID) -> ReplaySafeAgentState:
    return ReplaySafeAgentState(
        thread_id=thread_id,
        trace_id=trace_id,
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        user_id=uuid4(),
        property_context_verified=False,
        workflow_stage=WorkflowStage.INTAKE,
        task_intent=AgentIntent.UNKNOWN,
        utterance_intent=AgentIntent.UNKNOWN,
        intent_version=1,
        safety_review_required=False,
        policy_conflict=False,
        pending_action=PendingAction.NONE,
    )


async def _capturing_bundle(
    repository: ReplayRepository,
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[ReplayBundleView, ReplaySafeAgentState]:
    run_id, thread_id, trace_id = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    runtime = TraceRuntime(sessions, TraceSanitizer(max_payload_bytes=2048, max_string_length=128))
    await runtime.start_run(
        StartRun(
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            trigger=AgentRunTrigger.MESSAGE,
            actor_type=ActorType.RESIDENT.value,
            actor_id=uuid4(),
            started_at=now,
        )
    )
    safe = _safe(thread_id=thread_id, trace_id=trace_id)
    bundle = await repository.create_bundle(
        original_run_id=run_id,
        thread_id=thread_id,
        original_trace_id=trace_id,
        trigger_type=AgentRunTrigger.MESSAGE,
        input_envelope=MessageReplayInput(
            message=ReplayMessageMetadata(
                message_id=uuid4(),
                content_hash="a" * 64,
                content_length=1,
                language="zh-CN",
                message_role="USER",
            ),
            reference_time=now,
            timezone_name="UTC",
        ),
        schema_version=1,
        graph_schema_version=1,
        runtime_revision="test",
        captured_at=now,
    )
    return bundle, safe


@pytest.mark.asyncio
async def test_repository_captures_validates_and_completes_replay(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = ReplayRepository(sessions)
    bundle, safe = await _capturing_bundle(repository, sessions)
    await repository.set_start_state(bundle.bundle_id, safe)
    step = NodeEnteredStep(node_name="resolve_property", state_fingerprint="b" * 64)
    first = await repository.append_step(
        bundle.bundle_id,
        step_key="node:1",
        payload=step,
        request_fingerprint=None,
        occurred_at=datetime.now(UTC),
    )
    duplicate = await repository.append_step(
        bundle.bundle_id,
        step_key="node:1",
        payload=step,
        request_fingerprint=None,
        occurred_at=datetime.now(UTC),
    )
    assert first == duplicate
    finalized = await repository.finalize_bundle(
        bundle.bundle_id,
        status=ReplayBundleStatus.READY,
        expected_result=safe,
        expected_route_fingerprint="c" * 64,
        expected_state_fingerprint="d" * 64,
        capture_error_code=None,
        finalized_at=datetime.now(UTC),
    )
    assert finalized.status is ReplayBundleStatus.READY
    tape = await repository.load_steps(finalized.bundle_id)
    assert [item.sequence_number for item in tape] == [1]
    assert await repository.validate_integrity(finalized, tape)
    assert (await repository.get_bundle_for_run(finalized.original_run_id)) == finalized

    actor_id, user_id = uuid4(), uuid4()
    execution, created = await repository.create_execution(
        bundle_id=finalized.bundle_id,
        actor_id=actor_id,
        user_id=user_id,
        request_key_fingerprint="e" * 64,
        request_fingerprint="f" * 64,
        runtime_revision="test",
        graph_schema_version=1,
        started_at=datetime.now(UTC),
    )
    assert created
    comparison = ReplayComparisonResult(
        status=ReplayExecutionStatus.PASSED,
        node_path=("resolve_property",),
        route_fingerprint="c" * 64,
        state_fingerprint="d" * 64,
        consumed_steps=1,
        total_steps=1,
    )
    completed = await repository.complete_execution(
        execution.execution_id,
        comparison=comparison,
        recommendation=RecoveryRecommendation.NO_ACTION_REQUIRED,
        completed_at=datetime.now(UTC),
    )
    assert completed.status is ReplayExecutionStatus.PASSED
    repeated = await repository.complete_execution(
        execution.execution_id,
        comparison=comparison.model_copy(update={"status": ReplayExecutionStatus.DIVERGED}),
        recommendation=RecoveryRecommendation.REPLAY_DIVERGED_REVIEW_REQUIRED,
        completed_at=datetime.now(UTC),
    )
    assert repeated.status is ReplayExecutionStatus.PASSED
    async with sessions() as session:
        replay_events = await session.scalar(
            select(func.count())
            .select_from(AgentTraceEvent)
            .where(AgentTraceEvent.source == "REPLAY")
        )
    assert replay_events is not None and replay_events >= 5
    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_rejects_step_key_content_change_and_limits(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = ReplayRepository(sessions, max_steps=1, max_payload_bytes=4096)
    bundle, safe = await _capturing_bundle(repository, sessions)
    await repository.set_start_state(bundle.bundle_id, safe)
    await repository.append_step(
        bundle.bundle_id,
        step_key="node:1",
        payload=NodeEnteredStep(node_name="a", state_fingerprint="a" * 64),
        request_fingerprint=None,
        occurred_at=datetime.now(UTC),
    )
    with pytest.raises(ReplayConflict):
        await repository.append_step(
            bundle.bundle_id,
            step_key="node:1",
            payload=NodeEnteredStep(node_name="b", state_fingerprint="a" * 64),
            request_fingerprint=None,
            occurred_at=datetime.now(UTC),
        )
    with pytest.raises(ReplayConflict):
        await repository.append_step(
            bundle.bundle_id,
            step_key="node:2",
            payload=NodeEnteredStep(node_name="c", state_fingerprint="a" * 64),
            request_fingerprint=None,
            occurred_at=datetime.now(UTC),
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_finalize_has_one_immutable_terminal_result(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = ReplayRepository(sessions)
    bundle, safe = await _capturing_bundle(repository, sessions)
    await repository.set_start_state(bundle.bundle_id, safe)
    await repository.append_step(
        bundle.bundle_id,
        step_key="node:1",
        payload=NodeEnteredStep(node_name="a", state_fingerprint="a" * 64),
        request_fingerprint=None,
        occurred_at=datetime.now(UTC),
    )

    async def finalize(status: ReplayBundleStatus) -> ReplayBundleStatus:
        result = await repository.finalize_bundle(
            bundle.bundle_id,
            status=status,
            expected_result=safe if status is ReplayBundleStatus.READY else None,
            expected_route_fingerprint="b" * 64 if status is ReplayBundleStatus.READY else None,
            expected_state_fingerprint="c" * 64 if status is ReplayBundleStatus.READY else None,
            capture_error_code=None if status is ReplayBundleStatus.READY else "CAPTURE_FAILED",
            finalized_at=datetime.now(UTC),
        )
        return result.status

    outcomes = await asyncio.gather(
        finalize(ReplayBundleStatus.READY),
        finalize(ReplayBundleStatus.INCOMPLETE),
    )
    assert outcomes[0] is outcomes[1]
    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_execution_request_is_idempotent_and_payload_bound(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = ReplayRepository(sessions)
    bundle, safe = await _capturing_bundle(repository, sessions)
    await repository.set_start_state(bundle.bundle_id, safe)
    await repository.append_step(
        bundle.bundle_id,
        step_key="node:1",
        payload=NodeEnteredStep(node_name="a", state_fingerprint="a" * 64),
        request_fingerprint=None,
        occurred_at=datetime.now(UTC),
    )
    ready = await repository.finalize_bundle(
        bundle.bundle_id,
        status=ReplayBundleStatus.READY,
        expected_result=safe,
        expected_route_fingerprint="b" * 64,
        expected_state_fingerprint="c" * 64,
        capture_error_code=None,
        finalized_at=datetime.now(UTC),
    )
    actor_id, user_id = uuid4(), uuid4()

    async def create() -> tuple[ReplayExecutionView, bool]:
        return await repository.create_execution(
            bundle_id=ready.bundle_id,
            actor_id=actor_id,
            user_id=user_id,
            request_key_fingerprint="d" * 64,
            request_fingerprint="e" * 64,
            runtime_revision="test",
            graph_schema_version=1,
            started_at=datetime.now(UTC),
        )

    results = await asyncio.gather(*(create() for _ in range(5)))
    assert sum(created for _, created in results) == 1
    assert len({item.execution_id for item, _ in results}) == 1
    with pytest.raises(ReplayIdempotencyConflict):
        await repository.create_execution(
            bundle_id=ready.bundle_id,
            actor_id=actor_id,
            user_id=user_id,
            request_key_fingerprint="d" * 64,
            request_fingerprint="f" * 64,
            runtime_revision="test",
            graph_schema_version=1,
            started_at=datetime.now(UTC),
        )
    await engine.dispose()

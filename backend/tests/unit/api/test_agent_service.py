import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.agent_runtime.models import (
    AgentRunResult,
    AgentStateView,
    NeedInformationInterrupt,
    RunStatus,
)
from app.agent_runtime.orchestration import AgentOrchestrator
from app.api.errors import ApiError
from app.api.schemas.agent import ProvideInformationResumeRequest, SSEEvent
from app.api.services.agent import AgentApiService
from app.api.services.idempotency import ApiIdempotencyStore
from app.api.services.sse import SSEEventBus
from app.application.agent_reliability import (
    AgentReliabilityService,
    HumanReviewPersistenceFailed,
)
from app.application.agent_reliability_models import FinalizeAgentRun
from app.application.auth import AuthenticatedIdentity
from app.application.services import FixFlowApplicationService
from app.domain.enums import ActorType, WorkflowStage
from app.trace.runtime import TraceRuntime


class FakeOrchestrator:
    def __init__(self, result: AgentRunResult) -> None:
        self.result = result
        self.start_calls = 0

    async def start_turn(self, turn: object) -> AgentRunResult:
        del turn
        self.start_calls += 1
        return self.result

    async def resume(self, thread_id: UUID, caller: object, resume: object) -> AgentRunResult:
        del thread_id, caller, resume
        return self.result

    async def get_state(self, thread_id: UUID, caller: object, trace_id: UUID) -> AgentStateView:
        del caller, trace_id
        return AgentStateView(
            thread_id=thread_id,
            intent_version=1,
            workflow_stage=self.result.workflow_stage,
            property_id=uuid4(),
            property_context_verified=True,
            active_ticket_id=None,
            active_appointment_id=None,
            ticket_snapshot_version=None,
            appointment_version=None,
            last_assistant_message=self.result.assistant_message,
            run_status=self.result.run_status,
            interrupt=self.result.interrupt,
        )


class FailingTrace:
    async def start_run(self, command: object) -> None:
        del command
        raise RuntimeError("trace unavailable")


class FlakyTrace:
    def __init__(self) -> None:
        self.start_attempts = 0

    async def start_run(self, command: object) -> None:
        del command
        self.start_attempts += 1
        if self.start_attempts == 1:
            raise RuntimeError("trace unavailable")

    async def append_event(self, **kwargs: object) -> None:
        del kwargs

    async def finish_run(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    @staticmethod
    def event_key(*parts: object) -> str:
        return ":".join(str(part) for part in parts)


class ReviewPersistenceFailure:
    def __init__(self) -> None:
        self.commands: list[FinalizeAgentRun] = []

    async def finalize_run(self, command: FinalizeAgentRun) -> None:
        self.commands.append(command)
        if len(self.commands) == 1:
            raise HumanReviewPersistenceFailed

    async def list_messages(self, **kwargs: object) -> tuple[()]:
        del kwargs
        return ()

    async def require_active_thread(self, **kwargs: object) -> None:
        del kwargs


class RecordingReliability(ReviewPersistenceFailure):
    async def finalize_run(self, command: FinalizeAgentRun) -> None:
        self.commands.append(command)


def _service(result: AgentRunResult, events: SSEEventBus) -> AgentApiService:
    return AgentApiService(
        cast(AgentOrchestrator, FakeOrchestrator(result)),
        cast(FixFlowApplicationService, object()),
        events,
    )


def _identity() -> AuthenticatedIdentity:
    actor_id = uuid4()
    return AuthenticatedIdentity(actor_id, "resident", ActorType.RESIDENT)


async def _event_types(queue: asyncio.Queue[SSEEvent]) -> list[str]:
    types: list[str] = []
    while not queue.empty():
        types.append((await queue.get()).event_type)
    return types


@pytest.mark.asyncio
async def test_completed_turn_publishes_assistant_and_workflow_events() -> None:
    thread_id, trace_id = uuid4(), uuid4()
    events = SSEEventBus()
    result = AgentRunResult(
        thread_id=thread_id,
        trace_id=trace_id,
        run_status=RunStatus.COMPLETED,
        assistant_message="已完成",
        workflow_stage=WorkflowStage.DONE,
    )
    async with events.subscribe(thread_id) as queue:
        await _service(result, events).send_message(
            _identity(),
            thread_id=thread_id,
            message="查询状态",
            message_id=uuid4(),
            reference_time=datetime.now(UTC),
            timezone_name="UTC",
        )
        assert await _event_types(queue) == [
            "run_started",
            "workflow_updated",
            "assistant_delta",
            "message.completed",
        ]


@pytest.mark.asyncio
async def test_interrupted_turn_publishes_interrupt_required() -> None:
    thread_id, trace_id = uuid4(), uuid4()
    events = SSEEventBus()
    interrupt = NeedInformationInterrupt(
        intent_version=1,
        missing_fields=("issue_location",),
        message="请补充位置",
    )
    result = AgentRunResult(
        thread_id=thread_id,
        trace_id=trace_id,
        run_status=RunStatus.INTERRUPTED,
        interrupt=interrupt,
        workflow_stage=WorkflowStage.NEED_INFO,
    )
    async with events.subscribe(thread_id) as queue:
        await _service(result, events).send_message(
            _identity(),
            thread_id=thread_id,
            message="漏水",
            message_id=uuid4(),
            reference_time=datetime.now(UTC),
            timezone_name="UTC",
        )
        assert await _event_types(queue) == [
            "run_started",
            "workflow_updated",
            "assistant_delta",
            "message.completed",
        ]


@pytest.mark.asyncio
async def test_failed_safe_turn_publishes_only_safe_failure() -> None:
    thread_id, trace_id = uuid4(), uuid4()
    events = SSEEventBus()
    result = AgentRunResult(
        thread_id=thread_id,
        trace_id=trace_id,
        run_status=RunStatus.FAILED_SAFE,
        workflow_stage=WorkflowStage.HUMAN_REVIEW,
        error_code="SERVICE_UNAVAILABLE",
    )
    async with events.subscribe(thread_id) as queue:
        await _service(result, events).send_message(
            _identity(),
            thread_id=thread_id,
            message="漏水",
            message_id=uuid4(),
            reference_time=datetime.now(UTC),
            timezone_name="UTC",
        )
        queued = [await queue.get(), await queue.get(), await queue.get(), await queue.get()]
        assert [event.event_type for event in queued] == [
            "run_started",
            "workflow_updated",
            "assistant_delta",
            "message.failed",
        ]
        assert queued[-1].data["error_code"] == "SERVICE_UNAVAILABLE"
        assert queued[-1].data["message_outcome"] == "FAILED"


@pytest.mark.asyncio
async def test_escalation_is_changed_to_failed_when_review_case_cannot_persist() -> None:
    thread_id, trace_id = uuid4(), uuid4()
    events = SSEEventBus()
    reliability = ReviewPersistenceFailure()
    result = AgentRunResult(
        thread_id=thread_id,
        trace_id=trace_id,
        run_status=RunStatus.NEEDS_HUMAN_REVIEW,
        assistant_message="已转交物业处理。",
        workflow_stage=WorkflowStage.HUMAN_REVIEW,
        error_code="POLICY_REVIEW_REQUIRED",
    )
    service = AgentApiService(
        cast(AgentOrchestrator, FakeOrchestrator(result)),
        cast(FixFlowApplicationService, object()),
        events,
        reliability=cast(AgentReliabilityService, reliability),
    )
    response = await service.send_message(
        _identity(),
        thread_id=thread_id,
        message="继续",
        message_id=uuid4(),
        reference_time=datetime.now(UTC),
        timezone_name="UTC",
    )
    assert response.message_outcome.value == "FAILED"
    assert response.required_user_action.value == "RETRY"
    assert response.error_code == "HUMAN_REVIEW_PERSISTENCE_FAILED"
    assert [item.outcome.value for item in reliability.commands] == [
        "ESCALATED",
        "FAILED",
    ]


@pytest.mark.asyncio
async def test_provider_exhaustion_creates_escalated_reliability_result() -> None:
    thread_id, trace_id = uuid4(), uuid4()
    reliability = RecordingReliability()
    result = AgentRunResult(
        thread_id=thread_id,
        trace_id=trace_id,
        run_status=RunStatus.FAILED_SAFE,
        workflow_stage=WorkflowStage.INTAKE,
        error_code="PROVIDER_EXHAUSTED",
    )
    service = AgentApiService(
        cast(AgentOrchestrator, FakeOrchestrator(result)),
        cast(FixFlowApplicationService, object()),
        SSEEventBus(),
        reliability=cast(AgentReliabilityService, reliability),
    )
    response = await service.send_message(
        _identity(),
        thread_id=thread_id,
        message="厨房漏水",
        message_id=uuid4(),
        reference_time=datetime.now(UTC),
        timezone_name="UTC",
    )
    assert response.message_outcome.value == "ESCALATED"
    assert response.required_user_action.value == "CONTACT_OPERATOR"
    assert reliability.commands[-1].error_code == "PROVIDER_EXHAUSTED"


@pytest.mark.asyncio
async def test_stale_resume_maps_to_stable_api_conflict() -> None:
    result = AgentRunResult(
        thread_id=uuid4(),
        trace_id=uuid4(),
        run_status=RunStatus.FAILED_SAFE,
        workflow_stage=WorkflowStage.AWAITING_SLOT_CONFIRMATION,
        error_code="RESUME_CONFLICT",
    )
    request = ProvideInformationResumeRequest(
        kind="PROVIDE_INFORMATION",
        intent_version=1,
        user_message="补充信息",
        reference_time=datetime.now(UTC),
        timezone_name="UTC",
    )
    with pytest.raises(ApiError) as raised:
        await _service(result, SSEEventBus()).resume(
            _identity(), thread_id=result.thread_id, request=request
        )
    assert raised.value.status_code == 409
    assert raised.value.code == "STALE_RESUME"


@pytest.mark.asyncio
async def test_trace_start_failure_prevents_graph_execution() -> None:
    result = AgentRunResult(
        thread_id=uuid4(),
        trace_id=uuid4(),
        run_status=RunStatus.COMPLETED,
        workflow_stage=WorkflowStage.DONE,
    )
    orchestrator = FakeOrchestrator(result)
    trace = cast(TraceRuntime, FailingTrace())
    service = AgentApiService(
        cast(AgentOrchestrator, orchestrator),
        cast(FixFlowApplicationService, object()),
        SSEEventBus(),
        trace,
    )
    with pytest.raises(ApiError) as raised:
        await service.send_message(
            _identity(),
            thread_id=result.thread_id,
            message="查询状态",
            message_id=uuid4(),
            reference_time=datetime.now(UTC),
            timezone_name="UTC",
        )
    assert raised.value.status_code == 503
    assert orchestrator.start_calls == 0


@pytest.mark.asyncio
async def test_failed_trace_start_does_not_permanently_reserve_api_idempotency_key() -> None:
    result = AgentRunResult(
        thread_id=uuid4(),
        trace_id=uuid4(),
        run_status=RunStatus.COMPLETED,
        workflow_stage=WorkflowStage.DONE,
    )
    orchestrator = FakeOrchestrator(result)
    service = AgentApiService(
        cast(AgentOrchestrator, orchestrator),
        cast(FixFlowApplicationService, object()),
        SSEEventBus(),
        cast(TraceRuntime, FlakyTrace()),
    )
    store = ApiIdempotencyStore()
    identity = _identity()

    async def execute() -> object:
        return await service.send_message(
            identity,
            thread_id=result.thread_id,
            message="查询状态",
            message_id=uuid4(),
            reference_time=datetime.now(UTC),
            timezone_name="UTC",
        )

    with pytest.raises(ApiError) as raised:
        await store.execute(
            caller_id=identity.actor_id,
            scope=f"agent:message:{result.thread_id}",
            key="retry-after-trace-start-failure",
            payload={"message": "查询状态"},
            operation=execute,
        )
    assert raised.value.code == "SERVICE_UNAVAILABLE"
    await store.execute(
        caller_id=identity.actor_id,
        scope=f"agent:message:{result.thread_id}",
        key="retry-after-trace-start-failure",
        payload={"message": "查询状态"},
        operation=execute,
    )
    assert orchestrator.start_calls == 1

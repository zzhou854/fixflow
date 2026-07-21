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
from app.api.services.sse import SSEEventBus
from app.application.auth import AuthenticatedIdentity
from app.application.services import FixFlowApplicationService
from app.domain.enums import ActorType, WorkflowStage


class FakeOrchestrator:
    def __init__(self, result: AgentRunResult) -> None:
        self.result = result

    async def start_turn(self, turn: object) -> AgentRunResult:
        del turn
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
            "assistant_completed",
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
            "interrupt_required",
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
        queued = [await queue.get(), await queue.get(), await queue.get()]
        assert [event.event_type for event in queued] == [
            "run_started",
            "workflow_updated",
            "run_failed",
        ]
        assert queued[-1].data == {"code": "SERVICE_UNAVAILABLE"}


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

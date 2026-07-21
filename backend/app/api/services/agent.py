"""Authenticated Agent HTTP use cases and safe public view composition."""

from __future__ import annotations

from datetime import datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.agent_runtime.models import (
    AgentCallerContext,
    AgentResume,
    AgentRunResult,
    AgentStateView,
    AgentTurnInput,
    ProvideInformationResume,
    RunStatus,
    SelectAppointmentSlotResume,
    SelectDuplicateTicketResume,
)
from app.agent_runtime.orchestration import AgentOrchestrator
from app.api.errors import ApiError
from app.api.schemas.agent import (
    AgentThreadResponse,
    PolicyStatusResponse,
    ResumeRequest,
    StructuredIssueResponse,
)
from app.api.schemas.tickets import TicketListItemResponse
from app.api.services.sse import SSEEventBus
from app.application.auth import AuthenticatedIdentity
from app.application.query_models import QueryActor
from app.application.services import FixFlowApplicationService


class AgentApiService:
    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        application: FixFlowApplicationService,
        events: SSEEventBus,
    ) -> None:
        self._orchestrator = orchestrator
        self._application = application
        self.events = events

    async def create_thread(
        self,
        identity: AuthenticatedIdentity,
        *,
        property_id: UUID,
        message: str,
        reference_time: datetime,
        timezone_name: str,
    ) -> AgentThreadResponse:
        thread_id, trace_id, message_id = uuid4(), uuid4(), uuid4()
        result = await self._run_turn(
            identity,
            thread_id=thread_id,
            trace_id=trace_id,
            property_id=property_id,
            message=message,
            reference_time=reference_time,
            timezone_name=timezone_name,
        )
        return await self._response(identity, result, message_id=message_id)

    async def send_message(
        self,
        identity: AuthenticatedIdentity,
        *,
        thread_id: UUID,
        message: str,
        message_id: UUID | None,
        reference_time: datetime,
        timezone_name: str,
    ) -> AgentThreadResponse:
        stable_message_id = message_id or uuid4()
        trace_id = uuid5(NAMESPACE_URL, f"fixflow:api-message:{thread_id}:{stable_message_id}")
        result = await self._run_turn(
            identity,
            thread_id=thread_id,
            trace_id=trace_id,
            property_id=None,
            message=message,
            reference_time=reference_time,
            timezone_name=timezone_name,
        )
        return await self._response(identity, result, message_id=stable_message_id)

    async def resume(
        self, identity: AuthenticatedIdentity, *, thread_id: UUID, request: ResumeRequest
    ) -> AgentThreadResponse:
        trace_id = uuid4()
        resume = self._resume(request, trace_id)
        await self.events.publish(thread_id, trace_id, "run_started", {"resume_kind": request.kind})
        result = await self._orchestrator.resume(thread_id, self._caller(identity), resume)
        self._raise_resume_error(result)
        return await self._response(identity, result)

    async def get_thread(
        self, identity: AuthenticatedIdentity, *, thread_id: UUID, trace_id: UUID
    ) -> AgentThreadResponse | None:
        state = await self._orchestrator.get_state(thread_id, self._caller(identity), trace_id)
        if state is None:
            return None
        result = AgentRunResult(
            thread_id=thread_id,
            trace_id=trace_id,
            run_status=state.run_status,
            assistant_message=state.last_assistant_message,
            interrupt=state.interrupt,
            workflow_stage=state.workflow_stage,
            active_ticket_id=state.active_ticket_id,
            active_appointment_id=state.active_appointment_id,
        )
        return await self._response(identity, result, state=state, publish=False)

    async def _run_turn(
        self,
        identity: AuthenticatedIdentity,
        *,
        thread_id: UUID,
        trace_id: UUID,
        property_id: UUID | None,
        message: str,
        reference_time: datetime,
        timezone_name: str,
    ) -> AgentRunResult:
        await self.events.publish(thread_id, trace_id, "run_started", {})
        return await self._orchestrator.start_turn(
            AgentTurnInput(
                thread_id=thread_id,
                trace_id=trace_id,
                actor_type=identity.actor_type,
                actor_id=identity.actor_id,
                user_id=identity.user_id,
                property_id=property_id,
                user_message=message,
                reference_time=reference_time,
                timezone_name=timezone_name,
            )
        )

    async def _response(
        self,
        identity: AuthenticatedIdentity,
        result: AgentRunResult,
        *,
        message_id: UUID | None = None,
        state: AgentStateView | None = None,
        publish: bool = True,
    ) -> AgentThreadResponse:
        if state is None and result.run_status is not RunStatus.FAILED_SAFE:
            state = await self._orchestrator.get_state(
                result.thread_id, self._caller(identity), result.trace_id
            )
        ticket = None
        if result.active_ticket_id is not None:
            detail = await self._application.get_ticket_detail(
                QueryActor(identity.actor_type, identity.actor_id), result.active_ticket_id
            )
            ticket = TicketListItemResponse.model_validate(detail.ticket)
        policy = PolicyStatusResponse(
            sufficiency=state.policy_sufficiency if state else None,
            conflict=state.policy_conflict if state else False,
            evidence_ids=state.policy_evidence_ids if state else (),
        )
        structured = StructuredIssueResponse(
            issue_category=state.issue_category if state else None,
            issue_location=state.issue_location if state else None,
            issue_description=state.issue_description if state else None,
            severity=state.severity if state else None,
        )
        response = AgentThreadResponse(
            thread_id=result.thread_id,
            trace_id=result.trace_id,
            message_id=message_id,
            workflow_stage=result.workflow_stage,
            run_status=result.run_status,
            assistant_message=result.assistant_message,
            interrupt=result.interrupt.model_dump(mode="json") if result.interrupt else None,
            active_ticket=ticket,
            active_appointment=ticket.appointment if ticket else None,
            policy_status=policy,
            structured_issue=structured,
            safety_review_required=state.safety_review_required if state else False,
            error_code=result.error_code,
        )
        if publish:
            await self._publish_result(response)
        return response

    async def _publish_result(self, response: AgentThreadResponse) -> None:
        await self.events.publish(
            response.thread_id,
            response.trace_id,
            "workflow_updated",
            {"workflow_stage": response.workflow_stage.value},
        )
        if response.run_status is RunStatus.FAILED_SAFE:
            await self.events.publish(
                response.thread_id,
                response.trace_id,
                "run_failed",
                {"code": response.error_code or "INTERNAL_ERROR"},
            )
            return
        if response.assistant_message:
            await self.events.publish(
                response.thread_id,
                response.trace_id,
                "assistant_delta",
                {"text": response.assistant_message},
            )
        if response.interrupt:
            await self.events.publish(
                response.thread_id,
                response.trace_id,
                "interrupt_required",
                {"kind": response.interrupt.kind},
            )
            return
        await self.events.publish(
            response.thread_id,
            response.trace_id,
            "assistant_completed",
            {"text": response.assistant_message or ""},
        )

    @staticmethod
    def _caller(identity: AuthenticatedIdentity) -> AgentCallerContext:
        return AgentCallerContext(
            actor_type=identity.actor_type,
            actor_id=identity.actor_id,
            user_id=identity.user_id,
        )

    @staticmethod
    def _resume(request: ResumeRequest, trace_id: UUID) -> AgentResume:
        if request.kind == "PROVIDE_INFORMATION":
            return ProvideInformationResume(**request.model_dump(), trace_id=trace_id)
        if request.kind == "SELECT_DUPLICATE_TICKET":
            return SelectDuplicateTicketResume(**request.model_dump(), trace_id=trace_id)
        return SelectAppointmentSlotResume(**request.model_dump(), trace_id=trace_id)

    @staticmethod
    def _raise_resume_error(result: AgentRunResult) -> None:
        if result.run_status is not RunStatus.FAILED_SAFE:
            return
        if result.error_code == "THREAD_IDENTITY_CONFLICT":
            raise ApiError(403, "THREAD_IDENTITY_CONFLICT", "无权恢复该会话。")
        if result.error_code == "RESUME_CONFLICT":
            raise ApiError(409, "STALE_RESUME", "该恢复请求已经过期，请刷新当前会话。")
        if result.error_code in {"PROPERTY_CONTEXT_REQUIRED", "PROPERTY_CONTEXT_CONFLICT"}:
            raise ApiError(409, result.error_code, "房屋上下文已变化，请重新创建会话。")

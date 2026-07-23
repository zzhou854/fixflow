"""Authenticated Agent HTTP use cases and safe public view composition."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.agent.enums import LLMRole
from app.agent_runtime.execution_context import bind_execution_context
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
from app.infrastructure.database.models.observability import (
    AgentRunStatus,
    AgentRunTrigger,
    TraceSource,
)
from app.replay.capture import (
    NoOpReplayCapture,
    ReplayCapturePort,
    ReplayCaptureService,
    message_hash,
)
from app.replay.models import (
    MessageReplayInput,
    ProvideInformationReplayInput,
    ReplayMessageMetadata,
    SelectDuplicateReplayInput,
    SelectSlotReplayInput,
    ThreadCreatedReplayInput,
)
from app.trace.models import StartRun, TracePayload
from app.trace.runtime import TraceRuntime


class AgentApiService:
    def __init__(
        self,
        orchestrator: AgentOrchestrator,
        application: FixFlowApplicationService,
        events: SSEEventBus,
        trace: TraceRuntime | None = None,
        replay: ReplayCaptureService | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._application = application
        self.events = events
        self._trace = trace
        self._replay = replay

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
        result, run_id = await self._run_turn(
            identity,
            thread_id=thread_id,
            trace_id=trace_id,
            property_id=property_id,
            message=message,
            reference_time=reference_time,
            timezone_name=timezone_name,
            message_id=message_id,
        )
        return await self._response(identity, result, message_id=message_id, run_id=run_id)

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
        result, run_id = await self._run_turn(
            identity,
            thread_id=thread_id,
            trace_id=trace_id,
            property_id=None,
            message=message,
            reference_time=reference_time,
            timezone_name=timezone_name,
            message_id=stable_message_id,
        )
        return await self._response(identity, result, message_id=stable_message_id, run_id=run_id)

    async def resume(
        self, identity: AuthenticatedIdentity, *, thread_id: UUID, request: ResumeRequest
    ) -> AgentThreadResponse:
        trace_id = uuid4()
        run_id = uuid4()
        await self._start_trace(
            identity,
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            trigger=AgentRunTrigger.RESUME,
            property_id=None,
        )
        resume = self._resume(request, trace_id)
        capture = await self._start_replay_capture(
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            trigger=AgentRunTrigger.RESUME,
            input_envelope=self._resume_replay_input(request, trace_id),
        )
        await self.events.publish(thread_id, trace_id, "run_started", {"resume_kind": request.kind})
        try:
            with bind_execution_context(run_id, thread_id, trace_id, self._trace, capture):
                result = await self._orchestrator.resume(thread_id, self._caller(identity), resume)
        except Exception:
            await self._finish_trace(run_id, AgentRunStatus.FAILED, error_code="INTERNAL_ERROR")
            raise
        await capture.finalize(result)
        await self._finish_trace_for_result(run_id, result)
        self._raise_resume_error(result)
        response = await self._response(identity, result, run_id=run_id)
        return response

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

    async def record_api_replay(
        self,
        response: AgentThreadResponse,
        *,
        trace_id: UUID,
        idempotency_key_fingerprint: str,
    ) -> None:
        if self._trace is None:
            return
        await self._trace.append_event(
            event_key=self._trace.event_key(trace_id, "api_request_replayed"),
            run_id=None,
            thread_id=response.thread_id,
            trace_id=trace_id,
            source=TraceSource.API,
            event_type="api_request_replayed",
            payload=TracePayload(
                original_run_id=response.run_id,
                idempotency_key_fingerprint=idempotency_key_fingerprint,
                replayed=True,
            ),
            occurred_at=datetime.now(UTC),
        )

    async def record_api_conflict(
        self,
        *,
        thread_id: UUID | None,
        trace_id: UUID,
        idempotency_key_fingerprint: str,
    ) -> None:
        if self._trace is None:
            return
        await self._trace.append_event(
            event_key=self._trace.event_key(trace_id, "api_request_conflict"),
            run_id=None,
            thread_id=thread_id,
            trace_id=trace_id,
            source=TraceSource.API,
            event_type="api_request_conflict",
            payload=TracePayload(idempotency_key_fingerprint=idempotency_key_fingerprint),
            occurred_at=datetime.now(UTC),
        )

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
        message_id: UUID,
    ) -> tuple[AgentRunResult, UUID]:
        run_id = uuid4()
        trigger = (
            AgentRunTrigger.THREAD_CREATED if property_id is not None else AgentRunTrigger.MESSAGE
        )
        await self._start_trace(
            identity,
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            trigger=trigger,
            property_id=property_id,
        )
        await self.events.publish(thread_id, trace_id, "run_started", {})
        metadata = ReplayMessageMetadata(
            message_id=message_id,
            content_hash=message_hash(message),
            content_length=len(message),
            language="zh-CN",
            message_role=LLMRole.USER,
        )
        input_envelope = (
            ThreadCreatedReplayInput(
                property_id=property_id,
                message=metadata,
                reference_time=reference_time,
                timezone_name=timezone_name,
            )
            if property_id is not None
            else MessageReplayInput(
                message=metadata,
                reference_time=reference_time,
                timezone_name=timezone_name,
            )
        )
        capture = await self._start_replay_capture(
            run_id=run_id,
            thread_id=thread_id,
            trace_id=trace_id,
            trigger=trigger,
            input_envelope=input_envelope,
        )
        try:
            with bind_execution_context(run_id, thread_id, trace_id, self._trace, capture):
                result = await self._orchestrator.start_turn(
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
        except Exception:
            await self._finish_trace(run_id, AgentRunStatus.FAILED, error_code="INTERNAL_ERROR")
            raise
        await capture.finalize(result)
        await self._finish_trace_for_result(run_id, result)
        return result, run_id

    async def _start_replay_capture(
        self,
        *,
        run_id: UUID,
        thread_id: UUID,
        trace_id: UUID,
        trigger: AgentRunTrigger,
        input_envelope: object,
    ) -> ReplayCapturePort:
        if self._replay is None:
            return NoOpReplayCapture()
        return await self._replay.start(
            original_run_id=run_id,
            thread_id=thread_id,
            original_trace_id=trace_id,
            trigger_type=trigger,
            input_envelope=input_envelope,  # type: ignore[arg-type]
        )

    @staticmethod
    def _resume_replay_input(request: ResumeRequest, trace_id: UUID) -> object:
        if request.kind == "PROVIDE_INFORMATION":
            message = ReplayMessageMetadata(
                message_id=uuid5(NAMESPACE_URL, f"fixflow:replay-resume-message:{trace_id}"),
                content_hash=message_hash(request.user_message),
                content_length=len(request.user_message),
                language="zh-CN",
                message_role=LLMRole.USER,
            )
            return ProvideInformationReplayInput(
                intent_version=request.intent_version,
                message=message,
                reference_time=request.reference_time,
                timezone_name=request.timezone_name,
            )
        if request.kind == "SELECT_DUPLICATE_TICKET":
            return SelectDuplicateReplayInput(
                intent_version=request.intent_version,
                candidate_fingerprint=request.candidates_fingerprint,
                ticket_id=request.ticket_id,
            )
        return SelectSlotReplayInput(
            intent_version=request.intent_version,
            candidate_fingerprint=request.candidates_fingerprint,
            rank=request.rank,
        )

    async def _response(
        self,
        identity: AuthenticatedIdentity,
        result: AgentRunResult,
        *,
        message_id: UUID | None = None,
        state: AgentStateView | None = None,
        publish: bool = True,
        run_id: UUID | None = None,
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
        from app.api.schemas.agent import ResidentReconciliationResponse

        reconciliation = None
        case_id = (
            state.pending_reconciliation_case_id if state else result.pending_reconciliation_case_id
        )
        case_status = (
            state.pending_reconciliation_status if state else result.pending_reconciliation_status
        )
        case_action = (
            state.pending_reconciliation_action if state else result.pending_reconciliation_action
        )
        if case_id and case_status and case_action:
            reconciliation = ResidentReconciliationResponse(
                case_id=case_id,
                status=case_status,
                action=case_action,
                retry_allowed=False,
            )
        response = AgentThreadResponse(
            thread_id=result.thread_id,
            trace_id=result.trace_id,
            run_id=run_id,
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
            reconciliation=reconciliation,
        )
        if publish:
            await self._publish_result(response)
        return response

    async def _start_trace(
        self,
        identity: AuthenticatedIdentity,
        *,
        run_id: UUID,
        thread_id: UUID,
        trace_id: UUID,
        trigger: AgentRunTrigger,
        property_id: UUID | None,
    ) -> None:
        if self._trace is None:
            return
        now = datetime.now(UTC)
        try:
            await self._trace.start_run(
                StartRun(
                    run_id=run_id,
                    thread_id=thread_id,
                    trace_id=trace_id,
                    trigger=trigger,
                    actor_type=identity.actor_type.value,
                    actor_id=identity.actor_id,
                    user_id=identity.user_id,
                    property_id=property_id,
                    started_at=now,
                )
            )
            await self._trace.append_event(
                event_key=self._trace.event_key(run_id, "api_request_accepted"),
                run_id=run_id,
                thread_id=thread_id,
                trace_id=trace_id,
                source=TraceSource.API,
                event_type="api_request_accepted",
                payload=TracePayload(summary=trigger.value),
                occurred_at=now,
            )
        except Exception as exc:
            raise ApiError(
                503,
                "SERVICE_UNAVAILABLE",
                "执行审计暂时不可用，业务流程尚未启动。",
                retryable=True,
            ) from exc

    async def _finish_trace_for_result(self, run_id: UUID, result: AgentRunResult) -> None:
        status = AgentRunStatus.COMPLETED
        if result.interrupt is not None:
            status = AgentRunStatus.INTERRUPTED
        elif result.run_status is RunStatus.FAILED_SAFE:
            status = AgentRunStatus.FAILED_SAFE
        await self._finish_trace(run_id, status, error_code=result.error_code)

    async def _finish_trace(
        self, run_id: UUID, status: AgentRunStatus, *, error_code: str | None = None
    ) -> None:
        if self._trace is None:
            return
        await self._trace.finish_run(
            run_id,
            status=status,
            occurred_at=datetime.now(UTC),
            error_code=error_code,
        )

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

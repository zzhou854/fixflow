"""Authenticated Agent HTTP use cases and safe public view composition."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.agent.enums import LLMRole
from app.agent_runtime.errors import ThreadIdentityConflict
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
    ResidentConversationMessageResponse,
    ResidentThreadListResponse,
    ResidentThreadSummaryResponse,
    ResumeRequest,
    StructuredIssueResponse,
    ThreadLifecycleResponse,
)
from app.api.schemas.tickets import TicketListItemResponse
from app.api.services.sse import SSEEventBus
from app.application.agent_reliability import (
    AgentReliabilityError,
    AgentReliabilityService,
)
from app.application.agent_reliability_models import (
    FinalizeAgentRun,
    HumanReviewFailureStage,
    HumanReviewSafetyLevel,
    MessageOutcome,
    RequiredUserAction,
    ThreadLifecycleStatus,
)
from app.application.auth import AuthenticatedIdentity
from app.application.query_models import QueryActor
from app.application.services import FixFlowApplicationService
from app.domain.enums import WorkflowStage
from app.infrastructure.database.models.observability import (
    AgentRunStatus,
    AgentRunTrigger,
    TraceSource,
)
from app.llm.online.budget import ModelCallBudget, bind_model_call_budget
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
        reliability: AgentReliabilityService | None = None,
        model_budget_seconds: float = 25.0,
    ) -> None:
        self._orchestrator = orchestrator
        self._application = application
        self.events = events
        self._reliability = reliability
        self._trace = trace
        self._replay = replay
        self._model_budget_seconds = model_budget_seconds

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
        if self._reliability is not None:
            await self._reliability.register_thread(
                thread_id=thread_id,
                resident_id=identity.user_id,
                property_id=None,
            )
        result, run_id, outcome, required_action = await self._run_turn(
            identity,
            thread_id=thread_id,
            trace_id=trace_id,
            property_id=property_id,
            message=message,
            reference_time=reference_time,
            timezone_name=timezone_name,
            message_id=message_id,
        )
        return await self._response(
            identity,
            result,
            message_id=message_id,
            run_id=run_id,
            message_outcome=outcome,
            required_user_action=required_action,
        )

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
        if self._reliability is not None:
            await self._reliability.require_active_thread(
                thread_id=thread_id,
                resident_id=identity.user_id,
            )
        stable_message_id = message_id or uuid4()
        trace_id = uuid5(NAMESPACE_URL, f"fixflow:api-message:{thread_id}:{stable_message_id}")
        result, run_id, outcome, required_action = await self._run_turn(
            identity,
            thread_id=thread_id,
            trace_id=trace_id,
            property_id=None,
            message=message,
            reference_time=reference_time,
            timezone_name=timezone_name,
            message_id=stable_message_id,
        )
        return await self._response(
            identity,
            result,
            message_id=stable_message_id,
            run_id=run_id,
            message_outcome=outcome,
            required_user_action=required_action,
        )

    async def resume(
        self, identity: AuthenticatedIdentity, *, thread_id: UUID, request: ResumeRequest
    ) -> AgentThreadResponse:
        if self._reliability is not None:
            await self._reliability.require_active_thread(
                thread_id=thread_id,
                resident_id=identity.user_id,
            )
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
        await self.events.publish(
            thread_id,
            trace_id,
            "run_started",
            {"resume_kind": request.kind},
            run_id=run_id,
        )
        try:
            with (
                bind_execution_context(run_id, thread_id, trace_id, self._trace, capture),
                bind_model_call_budget(ModelCallBudget(self._model_budget_seconds)),
            ):
                result = await self._orchestrator.resume(thread_id, self._caller(identity), resume)
        except Exception:
            result = self._safe_failure_result(thread_id, trace_id)
        try:
            await capture.finalize(result)
        except Exception:
            pass
        state = await self._safe_state(identity, thread_id, trace_id, result)
        user_message = request.user_message if request.kind == "PROVIDE_INFORMATION" else None
        user_message_id = (
            uuid5(NAMESPACE_URL, f"fixflow:resume-message:{run_id}")
            if user_message is not None
            else None
        )
        result, outcome, required_action = await self._persist_result(
            identity,
            result,
            run_id=run_id,
            state=state,
            property_id=state.property_id if state and state.property_context_verified else None,
            user_message_id=user_message_id,
            user_message=user_message,
            user_message_created_at=(
                request.reference_time if request.kind == "PROVIDE_INFORMATION" else None
            ),
        )
        self._raise_resume_error(result)
        response = await self._response(
            identity,
            result,
            run_id=run_id,
            state=state,
            message_outcome=outcome,
            required_user_action=required_action,
        )
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
        latest = (
            await self._reliability.latest_public_result(
                thread_id=thread_id,
                resident_id=identity.user_id,
            )
            if self._reliability is not None
            else None
        )
        return await self._response(
            identity,
            result,
            state=state,
            publish=False,
            run_id=latest.run_id if latest else None,
            message_outcome=(latest.message_outcome if latest else self._message_outcome(result)),
            required_user_action=(
                latest.required_user_action if latest else self._required_user_action(result)
            ),
        )

    async def list_threads(
        self,
        identity: AuthenticatedIdentity,
        *,
        limit: int,
        offset: int,
        lifecycle_status: ThreadLifecycleStatus | None = ThreadLifecycleStatus.ACTIVE,
    ) -> ResidentThreadListResponse:
        if self._reliability is None:
            return ResidentThreadListResponse(items=(), limit=limit, offset=offset)
        records = await self._reliability.list_threads(
            resident_id=identity.user_id,
            lifecycle_status=lifecycle_status,
            limit=limit,
            offset=offset,
        )
        items: list[ResidentThreadSummaryResponse] = []
        for record in records:
            try:
                state = await self._orchestrator.get_state(
                    record.thread_id, self._caller(identity), uuid4()
                )
            except ThreadIdentityConflict:
                continue
            if state is None or (
                record.property_id is not None and state.property_id != record.property_id
            ):
                continue
            items.append(
                ResidentThreadSummaryResponse(
                    thread_id=record.thread_id,
                    property_id=record.property_id,
                    workflow_stage=state.workflow_stage,
                    run_status=state.run_status,
                    issue_category=state.issue_category,
                    issue_location=state.issue_location,
                    active_ticket_id=state.active_ticket_id,
                    updated_at=state.updated_at or record.updated_at,
                    lifecycle_status=record.lifecycle_status,
                    archived_at=record.archived_at,
                    version=record.version,
                )
            )
        return ResidentThreadListResponse(items=tuple(items), limit=limit, offset=offset)

    async def set_thread_lifecycle(
        self,
        identity: AuthenticatedIdentity,
        *,
        thread_id: UUID,
        lifecycle_status: ThreadLifecycleStatus,
        expected_version: int,
    ) -> ThreadLifecycleResponse:
        if self._reliability is None:
            raise ApiError(503, "SERVICE_UNAVAILABLE", "会话归档服务暂不可用。")
        record = await self._reliability.set_thread_lifecycle(
            thread_id=thread_id,
            resident_id=identity.user_id,
            lifecycle_status=lifecycle_status,
            actor_id=identity.actor_id,
            expected_version=expected_version,
        )
        return ThreadLifecycleResponse(
            thread_id=record.thread_id,
            lifecycle_status=record.lifecycle_status,
            archived_at=record.archived_at,
            version=record.version,
        )

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
    ) -> tuple[AgentRunResult, UUID, MessageOutcome, RequiredUserAction]:
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
        await self.events.publish(thread_id, trace_id, "run_started", {}, run_id=run_id)
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
            with (
                bind_execution_context(run_id, thread_id, trace_id, self._trace, capture),
                bind_model_call_budget(ModelCallBudget(self._model_budget_seconds)),
            ):
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
            result = self._safe_failure_result(thread_id, trace_id)
        try:
            await capture.finalize(result)
        except Exception:
            pass
        state = await self._safe_state(identity, thread_id, trace_id, result)
        result, outcome, required_action = await self._persist_result(
            identity,
            result,
            run_id=run_id,
            state=state,
            property_id=state.property_id if state and state.property_context_verified else None,
            user_message_id=message_id,
            user_message=message,
            user_message_created_at=reference_time,
        )
        return result, run_id, outcome, required_action

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

    async def _safe_state(
        self,
        identity: AuthenticatedIdentity,
        thread_id: UUID,
        trace_id: UUID,
        result: AgentRunResult,
    ) -> AgentStateView | None:
        if result.error_code == "THREAD_IDENTITY_CONFLICT":
            return None
        try:
            return await self._orchestrator.get_state(
                thread_id,
                self._caller(identity),
                trace_id,
            )
        except ThreadIdentityConflict:
            return None

    async def _persist_result(
        self,
        identity: AuthenticatedIdentity,
        result: AgentRunResult,
        *,
        run_id: UUID,
        state: AgentStateView | None,
        property_id: UUID | None,
        user_message_id: UUID | None,
        user_message: str | None,
        user_message_created_at: datetime | None,
    ) -> tuple[AgentRunResult, MessageOutcome, RequiredUserAction]:
        outcome = self._message_outcome(result)
        required_action = self._required_user_action(result)
        assistant_message = result.assistant_message or self._default_assistant_message(outcome)
        result = result.model_copy(update={"assistant_message": assistant_message})
        stage = self._failure_stage(result) if outcome is MessageOutcome.ESCALATED else None
        command = FinalizeAgentRun(
            run_id=run_id,
            thread_id=result.thread_id,
            trace_id=result.trace_id,
            resident_id=identity.user_id,
            property_id=property_id,
            intent_version=state.intent_version if state else 1,
            user_message_id=user_message_id,
            user_message=user_message,
            user_message_created_at=user_message_created_at,
            assistant_message=assistant_message,
            outcome=outcome,
            required_user_action=required_action,
            agent_run_status=self._agent_run_status(result).value,
            agent_run_terminal_event_type=self._agent_run_terminal_event(result),
            message_event_type=f"message.{outcome.value.casefold()}",
            error_code=result.error_code,
            failure_stage=stage,
            reason_code=(result.error_code or stage.value) if stage else None,
            safety_level=(
                HumanReviewSafetyLevel.EMERGENCY
                if result.workflow_stage is WorkflowStage.EMERGENCY_REVIEW
                else HumanReviewSafetyLevel.ELEVATED
                if stage
                in {
                    HumanReviewFailureStage.PROPERTY_AUTHORIZATION,
                    HumanReviewFailureStage.MUTATION_RECONCILIATION,
                }
                else HumanReviewSafetyLevel.STANDARD
            ),
            active_ticket_id=result.active_ticket_id,
        )
        if self._reliability is None:
            await self._finish_trace_for_result(run_id, result)
            return result, outcome, required_action
        try:
            await self._reliability.finalize_run(command)
            return result, outcome, required_action
        except AgentReliabilityError:
            if outcome is not MessageOutcome.ESCALATED:
                raise
            failed_message = "本次请求未能完成，人工处理任务也未成功创建，请稍后重试。"
            failed_result = result.model_copy(
                update={
                    "run_status": RunStatus.FAILED_SAFE,
                    "assistant_message": failed_message,
                    "error_code": "HUMAN_REVIEW_PERSISTENCE_FAILED",
                }
            )
            await self._reliability.finalize_run(
                FinalizeAgentRun(
                    run_id=run_id,
                    thread_id=result.thread_id,
                    trace_id=result.trace_id,
                    resident_id=identity.user_id,
                    property_id=property_id,
                    intent_version=state.intent_version if state else 1,
                    user_message_id=user_message_id,
                    user_message=user_message,
                    user_message_created_at=user_message_created_at,
                    assistant_message=failed_message,
                    outcome=MessageOutcome.FAILED,
                    required_user_action=RequiredUserAction.RETRY,
                    agent_run_status=AgentRunStatus.FAILED_SAFE.value,
                    agent_run_terminal_event_type="run_failed_safe",
                    message_event_type="message.failed",
                    error_code="HUMAN_REVIEW_PERSISTENCE_FAILED",
                )
            )
            return failed_result, MessageOutcome.FAILED, RequiredUserAction.RETRY

    @staticmethod
    def _message_outcome(result: AgentRunResult) -> MessageOutcome:
        if result.error_code == "THREAD_IDENTITY_CONFLICT":
            return MessageOutcome.FAILED
        if result.run_status is RunStatus.NEEDS_HUMAN_REVIEW:
            return MessageOutcome.ESCALATED
        if result.run_status is RunStatus.FAILED_SAFE:
            if result.error_code in {
                "LANGUAGE_INTERPRETATION_FAILED",
                "PROVIDER_EXHAUSTED",
                "POLICY_RESULT_STALE",
                "RECONCILIATION_PENDING",
                "MANUAL_REVIEW",
            }:
                return MessageOutcome.ESCALATED
            return MessageOutcome.FAILED
        return MessageOutcome.COMPLETED

    @staticmethod
    def _required_user_action(result: AgentRunResult) -> RequiredUserAction:
        if result.interrupt is not None:
            return {
                "NEED_INFORMATION": RequiredUserAction.PROVIDE_DETAILS,
                "DUPLICATE_TICKET_SELECTION": RequiredUserAction.CONFIRM_ACTION,
                "APPOINTMENT_SLOT_SELECTION": RequiredUserAction.SELECT_SLOT,
            }[result.interrupt.kind]
        outcome = AgentApiService._message_outcome(result)
        if outcome is MessageOutcome.ESCALATED:
            return RequiredUserAction.CONTACT_OPERATOR
        if outcome is MessageOutcome.FAILED:
            return RequiredUserAction.RETRY
        return RequiredUserAction.NONE

    @staticmethod
    def _failure_stage(result: AgentRunResult) -> HumanReviewFailureStage:
        code = result.error_code or ""
        if result.workflow_stage is WorkflowStage.EMERGENCY_REVIEW:
            return HumanReviewFailureStage.SAFETY_REVIEW
        if code in {"POLICY_REVIEW_REQUIRED", "POLICY_RESULT_STALE"}:
            return HumanReviewFailureStage.POLICY_REVIEW
        if code in {
            "PERMISSION_DENIED",
            "PROPERTY_CONTEXT_REQUIRED",
            "PROPERTY_CONTEXT_CONFLICT",
        }:
            return HumanReviewFailureStage.PROPERTY_AUTHORIZATION
        if code in {"RECONCILIATION_PENDING", "MANUAL_REVIEW"}:
            return HumanReviewFailureStage.MUTATION_RECONCILIATION
        if code in {"UNSUPPORTED_AUTOMATION", "RESIDENT_MANUAL_REQUEST"}:
            return HumanReviewFailureStage.UNSUPPORTED_REQUEST
        if result.workflow_stage in {
            WorkflowStage.FINDING_SLOTS,
            WorkflowStage.AWAITING_SLOT_CONFIRMATION,
        }:
            return HumanReviewFailureStage.SCHEDULING
        return HumanReviewFailureStage.INTERPRETATION

    @staticmethod
    def _agent_run_status(result: AgentRunResult) -> AgentRunStatus:
        if result.run_status is RunStatus.FAILED_SAFE:
            return AgentRunStatus.FAILED_SAFE
        if result.interrupt is not None:
            return AgentRunStatus.INTERRUPTED
        return AgentRunStatus.COMPLETED

    @staticmethod
    def _agent_run_terminal_event(result: AgentRunResult) -> str:
        return {
            AgentRunStatus.FAILED_SAFE: "run_failed_safe",
            AgentRunStatus.INTERRUPTED: "run_interrupted",
            AgentRunStatus.COMPLETED: "run_completed",
        }[AgentApiService._agent_run_status(result)]

    @staticmethod
    def _default_assistant_message(outcome: MessageOutcome) -> str:
        return {
            MessageOutcome.COMPLETED: "本次请求已处理完成。",
            MessageOutcome.ESCALATED: "本次请求已转交物业人工处理。",
            MessageOutcome.FAILED: "本次请求暂时未能完成，请稍后重试。",
        }[outcome]

    @staticmethod
    def _safe_failure_result(thread_id: UUID, trace_id: UUID) -> AgentRunResult:
        return AgentRunResult(
            thread_id=thread_id,
            trace_id=trace_id,
            run_status=RunStatus.FAILED_SAFE,
            assistant_message="本次请求暂时未能完成，请稍后重试。",
            workflow_stage=WorkflowStage.HUMAN_REVIEW,
            error_code="INTERNAL_ERROR",
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
        message_outcome: MessageOutcome | None = None,
        required_user_action: RequiredUserAction | None = None,
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
        durable_messages = (
            await self._reliability.list_messages(
                thread_id=result.thread_id,
                resident_id=identity.user_id,
            )
            if self._reliability is not None
            else ()
        )
        conversation_messages = tuple(
            ResidentConversationMessageResponse(
                role=item.role.value,
                content=item.content,
                created_at=item.created_at,
            )
            for item in durable_messages
        ) or tuple(
            ResidentConversationMessageResponse(
                role=item.role.value,
                content=item.content,
                created_at=item.created_at,
            )
            for item in (state.conversation_messages if state else ())
        )
        response = AgentThreadResponse(
            thread_id=result.thread_id,
            trace_id=result.trace_id,
            run_id=run_id,
            message_id=message_id,
            workflow_stage=result.workflow_stage,
            run_status=result.run_status,
            message_outcome=message_outcome or self._message_outcome(result),
            required_user_action=required_user_action or self._required_user_action(result),
            assistant_message=result.assistant_message,
            interrupt=result.interrupt.model_dump(mode="json") if result.interrupt else None,
            active_ticket=ticket,
            active_appointment=ticket.appointment if ticket else None,
            policy_status=policy,
            structured_issue=structured,
            safety_review_required=state.safety_review_required if state else False,
            error_code=result.error_code,
            reconciliation=reconciliation,
            conversation_messages=conversation_messages,
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
            run_id=response.run_id,
        )
        if response.assistant_message:
            await self.events.publish(
                response.thread_id,
                response.trace_id,
                "assistant_delta",
                {"text": response.assistant_message},
                run_id=response.run_id,
            )
        event_type = f"message.{response.message_outcome.value.casefold()}"
        await self.events.publish(
            response.thread_id,
            response.trace_id,
            event_type,
            {
                "text": response.assistant_message or "",
                "message_outcome": response.message_outcome.value,
                "required_user_action": response.required_user_action.value,
                "interrupt_kind": response.interrupt.kind if response.interrupt else None,
                "error_code": response.error_code,
            },
            run_id=response.run_id,
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

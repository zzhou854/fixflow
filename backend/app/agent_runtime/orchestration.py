"""Transport-neutral facade for starting, resuming, and inspecting Agent threads."""

from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

from app.agent.enums import LLMRole, PendingAction
from app.agent.state import AgentConversationMessage, AgentState
from app.agent_runtime.errors import AgentRuntimeError, ThreadIdentityConflict
from app.agent_runtime.mcp.client import PropertyOperationsClient
from app.agent_runtime.models import (
    AGENT_RESUME_ADAPTER,
    INTERRUPT_ADAPTER,
    AgentCallerContext,
    AgentResume,
    AgentRunResult,
    AgentStateView,
    AgentTurnInput,
    InterruptPayload,
    RunStatus,
    ThreadOwnerIdentity,
)
from app.agent_runtime.runtime_state import RuntimeGraphState
from app.domain.enums import WorkflowStage
from app.property_operations.contracts.common import ResultCode
from app.property_operations.contracts.properties import GetResidentPropertyRequest


class AgentOrchestrator:
    def __init__(
        self,
        graph: CompiledStateGraph[RuntimeGraphState, None, RuntimeGraphState, RuntimeGraphState],
        mcp: PropertyOperationsClient,
    ) -> None:
        self._graph = graph
        self._mcp = mcp

    @staticmethod
    def _config(thread_id: UUID) -> RunnableConfig:
        return RunnableConfig(configurable={"thread_id": str(thread_id)})

    async def _stored_state(self, thread_id: UUID) -> AgentState | None:
        snapshot = await self._graph.aget_state(self._config(thread_id))
        raw = snapshot.values.get("state_json") if snapshot.values else None
        return AgentState.model_validate_json(raw) if isinstance(raw, str) else None

    @staticmethod
    def _owner(state: AgentState) -> ThreadOwnerIdentity | None:
        if not state.property_context_verified or state.property_id is None:
            return None
        return ThreadOwnerIdentity(
            actor_type=state.actor_type,
            actor_id=state.actor_id,
            user_id=state.user_id,
            property_id=state.property_id,
        )

    @classmethod
    def _turn_matches_owner(cls, state: AgentState, turn: AgentTurnInput) -> bool:
        owner = cls._owner(state)
        if owner is None:
            return False
        return (
            owner.actor_type == turn.actor_type
            and owner.actor_id == turn.actor_id
            and owner.user_id == turn.user_id
            and (turn.property_id is None or turn.property_id == owner.property_id)
        )

    @classmethod
    def _caller_matches_owner(cls, state: AgentState, caller: AgentCallerContext) -> bool:
        owner = cls._owner(state)
        return owner is not None and (
            owner.actor_type == caller.actor_type
            and owner.actor_id == caller.actor_id
            and owner.user_id == caller.user_id
        )

    async def _preflight_property(self, state: AgentState, trace_id: UUID) -> str | None:
        if state.property_id is None:
            return "PROPERTY_CONTEXT_REQUIRED"
        response = await self._mcp.get_resident_property(
            GetResidentPropertyRequest(
                actor_type=state.actor_type,
                actor_id=state.actor_id,
                trace_id=trace_id,
                resident_id=state.user_id,
                property_id=state.property_id,
            )
        )
        if response.result_code is ResultCode.PERMISSION_DENIED:
            return "PERMISSION_DENIED"
        if response.result_code is not ResultCode.FOUND or response.data is None:
            return response.error.code if response.error else response.result_code.value
        if (
            response.data.resident_id != state.user_id
            or response.data.property_id != state.property_id
        ):
            return "PERMISSION_DENIED"
        return None

    async def _invalidate_authorization(self, state: AgentState) -> AgentState:
        """Persist only safe workflow invalidation after a relation is revoked."""

        safe_state = state.model_copy(
            update={
                "property_context_verified": False,
                "pending_action": PendingAction.NONE,
                "pending_operation": None,
                "user_confirmation": None,
                "candidate_slots": (),
                "candidate_slots_fingerprint": None,
                "selected_candidate_slot": None,
                "snapshot_refresh_required": True,
            }
        )
        await self._graph.aupdate_state(
            self._config(state.thread_id),
            RuntimeGraphState(state_json=safe_state.model_dump_json()),
        )
        return safe_state

    async def start_turn(self, turn: AgentTurnInput) -> AgentRunResult:
        existing = await self._stored_state(turn.thread_id)
        if existing is None and turn.property_id is None:
            return AgentRunResult(
                thread_id=turn.thread_id,
                trace_id=turn.trace_id,
                run_status=RunStatus.FAILED_SAFE,
                assistant_message="缺少可信房屋上下文，请从已认证入口重新选择房屋。",
                workflow_stage=WorkflowStage.NEED_PROPERTY,
                error_code="PROPERTY_CONTEXT_REQUIRED",
            )
        if existing is not None:
            if not self._turn_matches_owner(existing, turn):
                return self._public_failure(
                    turn.thread_id, turn.trace_id, "THREAD_IDENTITY_CONFLICT"
                )
            error = await self._preflight_property(existing, turn.trace_id)
            if error:
                invalidated = await self._invalidate_authorization(existing)
                return self._failure(turn, invalidated, error)
            saved = await self._graph.aget_state(self._config(turn.thread_id))
            if saved.next:
                if any(task.interrupts for task in saved.tasks):
                    return self._failure(turn, existing, "RESUME_REQUIRED")
                try:
                    retried = await self._graph.ainvoke(
                        None,
                        self._config(turn.thread_id),
                    )
                except AgentRuntimeError as exc:
                    current = await self._stored_state(turn.thread_id) or existing
                    return self._failure(turn, current, exc.code)
                return await self._result(
                    turn.thread_id,
                    turn.trace_id,
                    cast(RuntimeGraphState, retried),
                )
            state = existing.model_copy(
                update={
                    "trace_id": turn.trace_id,
                    # The formal MCP preflight immediately above has rechecked
                    # the relation for this turn; no node may infer it from the
                    # old checkpoint alone.
                    "property_context_verified": True,
                    "current_user_message": turn.user_message,
                    "current_reference_time": turn.reference_time,
                    "current_timezone_name": turn.timezone_name,
                }
            )
        else:
            state = AgentState(
                thread_id=turn.thread_id,
                trace_id=turn.trace_id,
                actor_type=turn.actor_type,
                actor_id=turn.actor_id,
                user_id=turn.user_id,
                property_id=turn.property_id,
                current_user_message=turn.user_message,
                current_reference_time=turn.reference_time,
                current_timezone_name=turn.timezone_name,
            )
        message_id = uuid5(NAMESPACE_URL, f"fixflow:{turn.thread_id}:{turn.trace_id}:USER")
        if not any(item.message_id == message_id for item in state.conversation_messages):
            state = state.model_copy(
                update={
                    "conversation_messages": (
                        *state.conversation_messages,
                        AgentConversationMessage(
                            message_id=message_id,
                            role=LLMRole.USER,
                            content=turn.user_message,
                            created_at=turn.reference_time,
                            turn_id=turn.trace_id,
                        ),
                    )[-100:]
                }
            )
        try:
            output = await self._graph.ainvoke(
                RuntimeGraphState(state_json=state.model_dump_json()),
                self._config(turn.thread_id),
            )
        except AgentRuntimeError as exc:
            current = await self._stored_state(turn.thread_id) or state
            return self._failure(turn, current, exc.code)
        return await self._result(turn.thread_id, turn.trace_id, cast(RuntimeGraphState, output))

    async def resume(
        self, thread_id: UUID, caller: AgentCallerContext, resume: AgentResume
    ) -> AgentRunResult:
        validated = AGENT_RESUME_ADAPTER.validate_python(resume)
        state = await self._stored_state(thread_id)
        if state is None:
            return AgentRunResult(
                thread_id=thread_id,
                trace_id=validated.trace_id,
                run_status=RunStatus.FAILED_SAFE,
                workflow_stage=WorkflowStage.INTAKE,
                error_code="RESUME_CONFLICT",
            )
        if not self._caller_matches_owner(state, caller):
            return self._public_failure(thread_id, validated.trace_id, "THREAD_IDENTITY_CONFLICT")
        snapshot = await self._graph.aget_state(self._config(thread_id))
        if not snapshot.next:
            return self._failure_from_ids(thread_id, validated.trace_id, state, "RESUME_CONFLICT")
        error = await self._preflight_property(state, validated.trace_id)
        if error:
            invalidated = await self._invalidate_authorization(state)
            return self._failure_from_ids(thread_id, validated.trace_id, invalidated, error)
        command: Command[object] = Command(resume=validated.model_dump(mode="json"))
        try:
            output = await self._graph.ainvoke(
                command,
                self._config(thread_id),
            )
        except AgentRuntimeError as exc:
            current = await self._stored_state(thread_id) or state
            return self._failure_from_ids(thread_id, validated.trace_id, current, exc.code)
        except ValueError:
            return self._failure_from_ids(thread_id, validated.trace_id, state, "RESUME_CONFLICT")
        return await self._result(thread_id, validated.trace_id, cast(RuntimeGraphState, output))

    async def get_state(
        self, thread_id: UUID, caller: AgentCallerContext, trace_id: UUID
    ) -> AgentStateView | None:
        state = await self._stored_state(thread_id)
        if state is None:
            return None
        if not self._caller_matches_owner(state, caller):
            raise ThreadIdentityConflict("thread owner identity does not match caller")
        error = await self._preflight_property(state, trace_id)
        if error:
            await self._invalidate_authorization(state)
            raise ThreadIdentityConflict("caller no longer has property access")
        return AgentStateView(
            thread_id=state.thread_id,
            intent_version=state.intent_version,
            workflow_stage=state.workflow_stage,
            property_id=state.property_id,
            property_context_verified=state.property_context_verified,
            active_ticket_id=state.active_ticket_id,
            active_appointment_id=state.active_appointment_id,
            ticket_snapshot_version=state.ticket_snapshot_version,
            appointment_version=state.appointment_version,
            last_assistant_message=state.last_assistant_message,
        )

    async def _result(
        self, thread_id: UUID, trace_id: UUID, output: RuntimeGraphState
    ) -> AgentRunResult:
        state = AgentState.model_validate_json(output["state_json"])
        snapshot = await self._graph.aget_state(self._config(thread_id))
        payload: InterruptPayload | None = None
        for task in snapshot.tasks:
            if task.interrupts:
                payload = INTERRUPT_ADAPTER.validate_python(task.interrupts[0].value)
                break
        status = RunStatus.INTERRUPTED if payload else RunStatus.COMPLETED
        if state.workflow_stage in {WorkflowStage.HUMAN_REVIEW, WorkflowStage.EMERGENCY_REVIEW}:
            status = RunStatus.NEEDS_HUMAN_REVIEW
        return AgentRunResult(
            thread_id=thread_id,
            trace_id=trace_id,
            run_status=status,
            assistant_message=state.last_assistant_message,
            interrupt=payload,
            workflow_stage=state.workflow_stage,
            active_ticket_id=state.active_ticket_id,
            active_appointment_id=state.active_appointment_id,
            error_code=state.escalation_reason,
        )

    @staticmethod
    def _failure(turn: AgentTurnInput, state: AgentState, code: str) -> AgentRunResult:
        return AgentOrchestrator._failure_from_ids(turn.thread_id, turn.trace_id, state, code)

    @staticmethod
    def _failure_from_ids(
        thread_id: UUID, trace_id: UUID, state: AgentState, code: str
    ) -> AgentRunResult:
        return AgentRunResult(
            thread_id=thread_id,
            trace_id=trace_id,
            run_status=RunStatus.FAILED_SAFE,
            assistant_message="请求未执行，线程上下文校验失败。",
            workflow_stage=state.workflow_stage,
            active_ticket_id=state.active_ticket_id,
            active_appointment_id=state.active_appointment_id,
            error_code=code,
        )

    @staticmethod
    def _public_failure(thread_id: UUID, trace_id: UUID, code: str) -> AgentRunResult:
        """Return no state, interrupt, ticket, or appointment to a non-owner."""

        return AgentRunResult(
            thread_id=thread_id,
            trace_id=trace_id,
            run_status=RunStatus.FAILED_SAFE,
            assistant_message="请求未执行，线程上下文校验失败。",
            workflow_stage=WorkflowStage.INTAKE,
            error_code=code,
        )

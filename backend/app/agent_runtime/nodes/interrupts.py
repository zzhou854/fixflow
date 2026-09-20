"""Strict interrupt payloads and resume handling."""

from typing import cast

from langgraph.types import interrupt

from app.agent.enums import AgentIntent, LLMRole, PendingAction
from app.agent.state import AgentState
from app.agent_runtime.models import (
    AGENT_RESUME_ADAPTER,
    AppointmentSlotSelectionInterrupt,
    CancelAppointmentSlotSelectionResume,
    DuplicateTicketItem,
    DuplicateTicketSelectionInterrupt,
    NeedInformationInterrupt,
    ProvideInformationResume,
    SelectAppointmentSlotResume,
    SelectDuplicateTicketResume,
    SlotItem,
)
from app.agent_runtime.rules import requested_clock_has_passed
from app.agent_runtime.runtime_state import (
    RuntimeGraphState,
    append_message,
    dump_state,
    finish_with_assistant_message,
    load_state,
)
from app.domain.enums import WorkflowStage


async def need_information(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    return _request_information(
        state,
        missing_fields=tuple(item.value for item in state.missing_fields),
        message="请补充缺失的报修信息。",
    )


async def need_availability_information(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    """Ask for a resident availability window after the ticket exists."""

    state = load_state(graph_state)
    if requested_clock_has_passed(state):
        message = "您填写的上门时间已经过去，请选择当前时间之后的日期和时间。"
    elif state.user_availability_windows and not state.candidate_slots:
        message = "这个时间段暂时没有可选的上门时间，请换一个日期或时间段。"
    else:
        message = "请告诉我您方便维修人员上门的日期和时间段。"
    return _request_information(
        state,
        missing_fields=("AVAILABILITY",),
        message=message,
    )


def _request_information(
    state: AgentState,
    *,
    missing_fields: tuple[str, ...],
    message: str,
) -> RuntimeGraphState:
    value = interrupt(
        NeedInformationInterrupt(
            intent_version=state.intent_version,
            missing_fields=missing_fields,
            message=message,
        ).model_dump(mode="json")
    )
    resume = AGENT_RESUME_ADAPTER.validate_python(value)
    if not isinstance(resume, ProvideInformationResume):
        raise ValueError("resume kind does not match NEED_INFORMATION")
    if resume.intent_version != state.intent_version:
        raise ValueError("resume intent_version is stale")
    state = state.model_copy(
        update={
            "trace_id": resume.trace_id,
            "current_user_message": resume.user_message,
            "current_reference_time": resume.reference_time,
            "current_timezone_name": resume.timezone_name,
            "workflow_stage": WorkflowStage.INTAKE,
        }
    )
    return dump_state(
        append_message(
            state,
            role=LLMRole.USER,
            content=resume.user_message,
            turn_id=resume.trace_id,
            created_at=resume.reference_time,
        )
    )


async def select_duplicate(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    fingerprint = cast(str, state.duplicate_candidates_fingerprint)
    value = interrupt(
        DuplicateTicketSelectionInterrupt(
            intent_version=state.intent_version,
            candidates_fingerprint=fingerprint,
            tickets=tuple(
                DuplicateTicketItem(
                    ticket_id=item.ticket_id,
                    ticket_version=item.ticket_version,
                    ticket_status=item.ticket_status.value,
                    issue_location=item.issue_location,
                )
                for item in state.duplicate_ticket_candidates
            ),
        ).model_dump(mode="json")
    )
    resume = AGENT_RESUME_ADAPTER.validate_python(value)
    if not isinstance(resume, SelectDuplicateTicketResume):
        raise ValueError("resume kind does not match duplicate selection")
    if (
        resume.intent_version != state.intent_version
        or resume.candidates_fingerprint != fingerprint
    ):
        raise ValueError("duplicate selection is stale")
    selected = next(
        (item for item in state.duplicate_ticket_candidates if item.ticket_id == resume.ticket_id),
        None,
    )
    if selected is None:
        raise ValueError("selected ticket is not a candidate")
    return dump_state(
        state.model_copy(
            update={
                "trace_id": resume.trace_id,
                "active_ticket_id": selected.ticket_id,
                "ticket_snapshot_version": selected.ticket_version,
                "workflow_stage": WorkflowStage.EXISTING_TICKET,
                "snapshot_refresh_required": True,
            }
        )
    )


async def select_slot(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    fingerprint = cast(str, state.candidate_slots_fingerprint)
    value = interrupt(
        AppointmentSlotSelectionInterrupt(
            intent_version=state.intent_version,
            candidates_fingerprint=fingerprint,
            slots=tuple(
                SlotItem(
                    rank=item.rank,
                    worker_id=item.worker_id,
                    scheduled_start=item.scheduled_start,
                    scheduled_end=item.scheduled_end,
                )
                for item in state.candidate_slots
            ),
        ).model_dump(mode="json")
    )
    resume = AGENT_RESUME_ADAPTER.validate_python(value)
    if not isinstance(
        resume,
        (SelectAppointmentSlotResume, CancelAppointmentSlotSelectionResume),
    ):
        raise ValueError("resume kind does not match slot selection")
    if (
        resume.intent_version != state.intent_version
        or resume.candidates_fingerprint != fingerprint
    ):
        raise ValueError("slot selection is stale")
    if isinstance(resume, CancelAppointmentSlotSelectionResume):
        message = (
            "已取消改期，原预约保持不变。"
            if state.task_intent is AgentIntent.RESCHEDULE_APPOINTMENT
            else "已暂不选择上门时间，报修工单已经保留。"
        )
        return dump_state(
            finish_with_assistant_message(
                state,
                message=message,
                updates={
                    "trace_id": resume.trace_id,
                    "task_intent": AgentIntent.UNKNOWN,
                    "workflow_stage": WorkflowStage.DONE,
                    "candidate_slots": (),
                    "candidate_slots_fingerprint": None,
                    "selected_candidate_slot": None,
                    "user_availability_windows": (),
                    "missing_fields": (),
                    "last_tool_result": None,
                    "pending_action": PendingAction.NONE,
                    "pending_operation": None,
                    "snapshot_refresh_required": False,
                },
            )
        )
    selected = next((item for item in state.candidate_slots if item.rank == resume.rank), None)
    if selected is None:
        raise ValueError("selected rank is not a candidate")
    return dump_state(
        state.model_copy(
            update={
                "trace_id": resume.trace_id,
                "selected_candidate_slot": selected,
                "snapshot_refresh_required": True,
            }
        )
    )

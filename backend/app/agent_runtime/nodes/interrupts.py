"""Strict interrupt payloads and resume handling."""

from typing import cast

from langgraph.types import interrupt

from app.agent.enums import LLMRole
from app.agent_runtime.models import (
    AGENT_RESUME_ADAPTER,
    AppointmentSlotSelectionInterrupt,
    DuplicateTicketItem,
    DuplicateTicketSelectionInterrupt,
    NeedInformationInterrupt,
    ProvideInformationResume,
    SelectAppointmentSlotResume,
    SelectDuplicateTicketResume,
    SlotItem,
)
from app.agent_runtime.runtime_state import (
    RuntimeGraphState,
    append_message,
    dump_state,
    load_state,
)
from app.domain.enums import WorkflowStage


async def need_information(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    value = interrupt(
        NeedInformationInterrupt(
            intent_version=state.intent_version,
            missing_fields=tuple(item.value for item in state.missing_fields),
            message="请补充缺失的报修信息。",
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
    if not isinstance(resume, SelectAppointmentSlotResume):
        raise ValueError("resume kind does not match slot selection")
    if (
        resume.intent_version != state.intent_version
        or resume.candidates_fingerprint != fingerprint
    ):
        raise ValueError("slot selection is stale")
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

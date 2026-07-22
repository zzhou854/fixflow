"""Candidate-slot querying and mutation nodes."""

from typing import cast

from app.agent.enums import PendingAction
from app.agent.state import CandidateSlot, ToolResultSummary
from app.agent_runtime.context import NodeContext
from app.agent_runtime.idempotency import build_pending_operation
from app.agent_runtime.runtime_state import (
    RuntimeGraphState,
    dump_state,
    fingerprint,
    load_state,
    require_data,
)
from app.domain.enums import WorkflowStage
from app.property_operations.contracts.appointments import (
    BookAppointmentRequest,
    ListAvailableSlotsRequest,
    RescheduleAppointmentRequest,
)
from app.property_operations.contracts.common import ResultCode


async def list_slots(context: NodeContext, graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    assert state.property_id and state.issue_category and state.service_duration_minutes
    if not state.user_availability_windows:
        return dump_state(state.model_copy(update={"workflow_stage": WorkflowStage.NEED_INFO}))
    window = state.user_availability_windows[0]
    response = await context.mcp.list_available_slots(
        ListAvailableSlotsRequest(
            actor_type=state.actor_type,
            actor_id=state.actor_id,
            trace_id=state.trace_id,
            property_id=state.property_id,
            issue_category=state.issue_category,
            search_window_start=window.starts_at,
            search_window_end=window.ends_at,
            requested_duration_minutes=state.service_duration_minutes,
        )
    )
    data = require_data(response)
    slots = tuple(
        CandidateSlot(
            worker_id=item.worker_id,
            scheduled_start=item.scheduled_start,
            scheduled_end=item.scheduled_end,
            rank=item.rank,
            booking_guaranteed=False,
        )
        for item in data.slots
    )
    return dump_state(
        state.model_copy(
            update={
                "candidate_slots": slots,
                "candidate_slots_fingerprint": fingerprint(
                    [item.model_dump(mode="json") for item in slots]
                ),
                "workflow_stage": WorkflowStage.AWAITING_SLOT_CONFIRMATION,
            }
        )
    )


async def prepare_book(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    slot = cast(CandidateSlot, state.selected_candidate_slot)
    operation = build_pending_operation(
        thread_id=state.thread_id,
        intent_version=state.intent_version,
        action=PendingAction.BOOK_APPOINTMENT,
        payload=slot.model_dump(mode="json"),
        target_id=state.active_ticket_id,
        expected_ticket_version=state.ticket_snapshot_version,
    )
    return dump_state(
        state.model_copy(
            update={
                "pending_action": PendingAction.BOOK_APPOINTMENT,
                "pending_operation": operation,
                "workflow_stage": WorkflowStage.BOOKING,
            }
        )
    )


async def book(context: NodeContext, graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    op, slot = state.pending_operation, state.selected_candidate_slot
    assert op and slot and state.active_ticket_id and op.expected_ticket_version
    response = await context.mcp.book_appointment(
        BookAppointmentRequest(
            actor_type=state.actor_type,
            actor_id=state.actor_id,
            trace_id=state.trace_id,
            idempotency_key=op.idempotency_key,
            request_fingerprint=op.request_fingerprint,
            ticket_id=state.active_ticket_id,
            worker_id=slot.worker_id,
            scheduled_start=slot.scheduled_start,
            scheduled_end=slot.scheduled_end,
            expected_version=op.expected_ticket_version,
        )
    )
    if response.result_code in {ResultCode.TIME_CONFLICT, ResultCode.VERSION_CONFLICT}:
        return dump_state(
            state.model_copy(
                update={
                    "candidate_slots": (),
                    "candidate_slots_fingerprint": None,
                    "selected_candidate_slot": None,
                    "pending_operation": None,
                    "pending_action": PendingAction.NONE,
                    "snapshot_refresh_required": True,
                }
            )
        )
    data = require_data(response)
    return dump_state(
        state.model_copy(
            update={
                "active_appointment_id": data.resource_id,
                "appointment_version": data.appointment_version or data.resource_version,
                "ticket_snapshot_version": data.ticket_version,
                "pending_operation": None,
                "pending_action": PendingAction.NONE,
                "candidate_slots": (),
                "candidate_slots_fingerprint": None,
                "selected_candidate_slot": None,
                "snapshot_refresh_required": True,
                "last_tool_result": ToolResultSummary(
                    result_code=response.result_code.value,
                    message=response.message,
                    resource_id=data.resource_id,
                    resource_version=data.resource_version,
                ),
            }
        )
    )


async def prepare_reschedule(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    slot = cast(CandidateSlot, state.selected_candidate_slot)
    operation = build_pending_operation(
        thread_id=state.thread_id,
        intent_version=state.intent_version,
        action=PendingAction.RESCHEDULE_APPOINTMENT,
        payload=slot.model_dump(mode="json"),
        target_id=state.active_appointment_id,
        expected_ticket_version=state.ticket_snapshot_version,
        expected_appointment_version=state.appointment_version,
    )
    return dump_state(
        state.model_copy(
            update={
                "pending_action": PendingAction.RESCHEDULE_APPOINTMENT,
                "pending_operation": operation,
                "workflow_stage": WorkflowStage.RESCHEDULING,
            }
        )
    )


async def reschedule(context: NodeContext, graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    op, slot = state.pending_operation, state.selected_candidate_slot
    assert op and slot and state.active_ticket_id and state.active_appointment_id
    assert op.expected_ticket_version and op.expected_appointment_version
    response = await context.mcp.reschedule_appointment(
        RescheduleAppointmentRequest(
            actor_type=state.actor_type,
            actor_id=state.actor_id,
            trace_id=state.trace_id,
            idempotency_key=op.idempotency_key,
            request_fingerprint=op.request_fingerprint,
            ticket_id=state.active_ticket_id,
            appointment_id=state.active_appointment_id,
            worker_id=slot.worker_id,
            scheduled_start=slot.scheduled_start,
            scheduled_end=slot.scheduled_end,
            expected_version=op.expected_ticket_version,
            expected_appointment_version=op.expected_appointment_version,
        )
    )
    if response.result_code in {ResultCode.TIME_CONFLICT, ResultCode.VERSION_CONFLICT}:
        return dump_state(
            state.model_copy(
                update={
                    "candidate_slots": (),
                    "candidate_slots_fingerprint": None,
                    "selected_candidate_slot": None,
                    "pending_operation": None,
                    "pending_action": PendingAction.NONE,
                    "snapshot_refresh_required": True,
                }
            )
        )
    data = require_data(response)
    return dump_state(
        state.model_copy(
            update={
                "active_appointment_id": data.resource_id,
                "appointment_version": data.appointment_version or data.resource_version,
                "ticket_snapshot_version": data.ticket_version or state.ticket_snapshot_version,
                "pending_operation": None,
                "pending_action": PendingAction.NONE,
                "candidate_slots": (),
                "candidate_slots_fingerprint": None,
                "selected_candidate_slot": None,
                "snapshot_refresh_required": True,
            }
        )
    )

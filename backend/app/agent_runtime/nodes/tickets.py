"""Duplicate detection, ticket creation, and database snapshot nodes."""

from datetime import UTC, datetime

from app.agent.enums import AgentIntent, PendingAction
from app.agent.state import (
    CachedAppointmentSnapshot,
    CachedTicketSnapshot,
    DuplicateTicketCandidate,
    ToolResultSummary,
)
from app.agent_runtime.context import NodeContext
from app.agent_runtime.idempotency import build_pending_operation
from app.agent_runtime.routing import is_terminal_ticket_status
from app.agent_runtime.runtime_state import (
    RuntimeGraphState,
    dump_state,
    fingerprint,
    load_state,
    require_data,
)
from app.domain.enums import WorkflowStage
from app.property_operations.contracts.tickets import (
    CreateRepairTicketRequest,
    FindOpenRepairTicketsRequest,
    GetTicketSnapshotRequest,
)


async def find_duplicates(
    context: NodeContext, graph_state: RuntimeGraphState
) -> RuntimeGraphState:
    state = load_state(graph_state)
    assert state.property_id and state.issue_category and state.normalized_issue_location
    response = await context.mcp.find_open_repair_tickets(
        FindOpenRepairTicketsRequest(
            actor_type=state.actor_type,
            actor_id=state.actor_id,
            trace_id=state.trace_id,
            resident_id=state.user_id,
            property_id=state.property_id,
            issue_category=state.issue_category,
            normalized_issue_location=state.normalized_issue_location,
        )
    )
    data = require_data(response)
    candidates = tuple(
        DuplicateTicketCandidate(
            ticket_id=item.ticket_id,
            ticket_version=item.ticket_version,
            ticket_status=item.ticket_status,
            issue_location=item.issue_location,
        )
        for item in data.tickets
    )
    updates: dict[str, object] = {
        "duplicate_ticket_candidates": candidates,
        "duplicate_candidates_fingerprint": (
            fingerprint([item.model_dump(mode="json") for item in candidates])
            if candidates
            else None
        ),
        "workflow_stage": WorkflowStage.DUPLICATE_CHECK,
    }
    if len(candidates) == 1:
        # Exact structured matching is deterministic, so no resident choice is
        # necessary when there is only one viable non-terminal candidate.
        updates.update(
            active_ticket_id=candidates[0].ticket_id,
            ticket_snapshot_version=candidates[0].ticket_version,
            snapshot_refresh_required=True,
        )
    return dump_state(state.model_copy(update=updates))


async def prepare_create(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    payload = {
        "property_id": str(state.property_id),
        "category": state.issue_category.value if state.issue_category else None,
        "location": state.normalized_issue_location,
        "description": state.issue_description,
        "severity": state.severity.value if state.severity else None,
    }
    operation = build_pending_operation(
        thread_id=state.thread_id,
        intent_version=state.intent_version,
        action=PendingAction.CREATE_TICKET,
        payload=payload,
        target_id=state.property_id,
        expected_ticket_version=None,
    )
    return dump_state(
        state.model_copy(
            update={
                "pending_action": PendingAction.CREATE_TICKET,
                "pending_operation": operation,
                "workflow_stage": WorkflowStage.CREATING_TICKET,
            }
        )
    )


async def create_ticket(context: NodeContext, graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    op = state.pending_operation
    assert op and state.property_id and state.issue_category and state.issue_location
    assert state.issue_description and state.severity
    response = await context.mcp.create_repair_ticket(
        CreateRepairTicketRequest(
            actor_type=state.actor_type,
            actor_id=state.actor_id,
            trace_id=state.trace_id,
            idempotency_key=op.idempotency_key,
            request_fingerprint=op.request_fingerprint,
            resident_id=state.user_id,
            property_id=state.property_id,
            issue_category=state.issue_category,
            issue_location=state.issue_location,
            issue_description=state.issue_description,
            severity=state.severity,
        )
    )
    data = require_data(response)
    return dump_state(
        state.model_copy(
            update={
                "active_ticket_id": data.resource_id,
                "ticket_snapshot_version": data.ticket_version or data.resource_version,
                "pending_action": PendingAction.NONE,
                "pending_operation": None,
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


async def refresh_snapshot(
    context: NodeContext, graph_state: RuntimeGraphState
) -> RuntimeGraphState:
    state = load_state(graph_state)
    if state.active_ticket_id is None:
        return graph_state
    response = await context.mcp.get_ticket_snapshot(
        GetTicketSnapshotRequest(
            actor_type=state.actor_type,
            actor_id=state.actor_id,
            trace_id=state.trace_id,
            ticket_id=state.active_ticket_id,
        )
    )
    data = require_data(response)
    active = data.active_appointment
    snapshot = CachedTicketSnapshot(
        ticket_id=data.ticket_id,
        ticket_version=data.ticket_version,
        ticket_status=data.ticket_status,
        severity=data.severity,
        rework_count=data.rework_count,
        active_appointment=(
            CachedAppointmentSnapshot(
                appointment_id=active.appointment_id,
                worker_id=active.worker_id,
                appointment_status=active.appointment_status,
                scheduled_start=active.scheduled_start,
                scheduled_end=active.scheduled_end,
                appointment_version=active.appointment_version,
            )
            if active
            else None
        ),
        observed_at=datetime.now(UTC),
    )
    stale_slot = (
        state.ticket_snapshot_version is not None
        and state.ticket_snapshot_version != data.ticket_version
        and state.selected_candidate_slot is not None
    )
    updates: dict[str, object] = {
        "cached_ticket_snapshot": snapshot,
        "ticket_snapshot_version": data.ticket_version,
        "active_appointment_id": active.appointment_id if active else None,
        "appointment_version": active.appointment_version if active else None,
        "severity": data.severity,
        "snapshot_refresh_required": False,
    }
    if stale_slot:
        updates.update(
            candidate_slots=(), candidate_slots_fingerprint=None, selected_candidate_slot=None
        )
    if state.task_intent is AgentIntent.NEW_REPAIR and is_terminal_ticket_status(
        data.ticket_status
    ):
        # Do not adopt a duplicate that closed while its interrupt was pending.
        updates.update(
            active_ticket_id=None,
            active_appointment_id=None,
            ticket_snapshot_version=None,
            appointment_version=None,
            cached_ticket_snapshot=None,
            duplicate_ticket_candidates=(),
            duplicate_candidates_fingerprint=None,
            candidate_slots=(),
            candidate_slots_fingerprint=None,
            selected_candidate_slot=None,
        )
    return dump_state(state.model_copy(update=updates))


async def resolve_existing(
    context: NodeContext, graph_state: RuntimeGraphState
) -> RuntimeGraphState:
    state = load_state(graph_state)
    if state.active_ticket_id is not None:
        return dump_state(state.model_copy(update={"snapshot_refresh_required": True}))
    if state.issue_category and state.normalized_issue_location:
        return await find_duplicates(context, graph_state)
    return dump_state(
        state.model_copy(
            update={
                "workflow_stage": WorkflowStage.HUMAN_REVIEW,
                "last_assistant_message": "无法确定目标工单，需要人工协助。",
            }
        )
    )

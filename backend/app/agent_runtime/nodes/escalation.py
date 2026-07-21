"""Explicit resident-requested escalation nodes only."""

from app.agent.enums import PendingAction
from app.agent_runtime.context import NodeContext
from app.agent_runtime.idempotency import build_pending_operation
from app.agent_runtime.runtime_state import RuntimeGraphState, dump_state, load_state, require_data
from app.domain.enums import WorkflowStage
from app.property_operations.contracts.tickets import EscalateToOperatorRequest


async def prepare_escalate(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    operation = build_pending_operation(
        thread_id=state.thread_id,
        intent_version=state.intent_version,
        action=PendingAction.REQUEST_HUMAN_REVIEW,
        payload={"reason": "resident_requested_human"},
        target_id=state.active_ticket_id,
        expected_ticket_version=state.ticket_snapshot_version,
    )
    return dump_state(
        state.model_copy(
            update={
                "pending_action": PendingAction.REQUEST_HUMAN_REVIEW,
                "pending_operation": operation,
            }
        )
    )


async def escalate(context: NodeContext, graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    op = state.pending_operation
    assert op and state.active_ticket_id and op.expected_ticket_version
    response = await context.mcp.escalate_to_operator(
        EscalateToOperatorRequest(
            actor_type=state.actor_type,
            actor_id=state.actor_id,
            trace_id=state.trace_id,
            idempotency_key=op.idempotency_key,
            ticket_id=state.active_ticket_id,
            expected_version=op.expected_ticket_version,
            reason_code="MANUAL_REVIEW",
            reason_text="Resident requested operator assistance.",
        )
    )
    require_data(response)
    return dump_state(
        state.model_copy(
            update={
                "pending_operation": None,
                "pending_action": PendingAction.NONE,
                "snapshot_refresh_required": True,
                "workflow_stage": WorkflowStage.HUMAN_REVIEW,
            }
        )
    )

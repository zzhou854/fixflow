"""Deterministic, ordinary-Python routers for the single FixFlow graph."""

from app.agent.enums import AgentIntent
from app.agent_runtime.runtime_state import RuntimeGraphState, load_state
from app.domain.enums import TicketStatus, WorkflowStage
from app.policy.enums import EvidenceSufficiency


def route_after_property(graph_state: RuntimeGraphState) -> str:
    return "interpret" if load_state(graph_state).property_context_verified else "__end__"


def route_after_interpret(graph_state: RuntimeGraphState) -> str:
    state = load_state(graph_state)
    if not state.property_context_verified or state.workflow_stage in {
        WorkflowStage.NEED_PROPERTY,
        WorkflowStage.HUMAN_REVIEW,
    }:
        return "finish"
    if state.safety_review_required:
        return "finish_safety"
    if state.missing_fields:
        return "need_information"
    if state.task_intent is AgentIntent.NEW_REPAIR:
        return "retrieve_policy"
    if state.task_intent in {
        AgentIntent.QUERY_TICKET_STATUS,
        AgentIntent.RESCHEDULE_APPOINTMENT,
        AgentIntent.REQUEST_HUMAN,
    }:
        return "resolve_existing"
    return "finish_unsupported"


def route_after_policy(graph_state: RuntimeGraphState) -> str:
    state = load_state(graph_state)
    if state.policy_conflict or state.policy_sufficiency is not EvidenceSufficiency.SUFFICIENT:
        return "finish_policy_review"
    return "find_duplicates"


def route_duplicates(graph_state: RuntimeGraphState) -> str:
    state = load_state(graph_state)
    if state.active_ticket_id is not None:
        return "refresh_snapshot"
    return "prepare_create" if not state.duplicate_ticket_candidates else "select_duplicate"


def route_after_snapshot(graph_state: RuntimeGraphState) -> str:
    state = load_state(graph_state)
    if state.task_intent is AgentIntent.NEW_REPAIR and state.active_ticket_id is None:
        # A duplicate candidate may have become terminal while the resident was
        # choosing it.  Re-query instead of adopting stale checkpoint facts.
        return "find_duplicates"
    if state.selected_candidate_slot is not None:
        return (
            "prepare_reschedule"
            if state.task_intent is AgentIntent.RESCHEDULE_APPOINTMENT
            else "prepare_book"
        )
    if (
        state.task_intent is AgentIntent.NEW_REPAIR
        and state.cached_ticket_snapshot is not None
        and state.cached_ticket_snapshot.active_appointment is not None
    ):
        return "compose"
    if state.task_intent is AgentIntent.QUERY_TICKET_STATUS:
        return "compose"
    if state.task_intent is AgentIntent.REQUEST_HUMAN:
        return "prepare_escalate"
    return "list_slots"


def route_resolved_existing(graph_state: RuntimeGraphState) -> str:
    state = load_state(graph_state)
    if state.active_ticket_id:
        return "refresh_snapshot"
    if state.duplicate_ticket_candidates:
        return "select_duplicate"
    return "finish"


def is_terminal_ticket_status(status: TicketStatus) -> bool:
    return status in {TicketStatus.CANCELLED, TicketStatus.CLOSED}

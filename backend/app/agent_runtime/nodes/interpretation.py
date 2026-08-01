"""Typed language interpretation node; it has no business-data dependency."""

from datetime import datetime
from typing import cast

from app.agent.merge import merge_interpretation, route_required_safety_review
from app.agent.models import (
    AgentStateSummary,
    ConversationMessage,
    InterpretMessageInput,
    KnownIssueFields,
)
from app.agent_runtime.context import NodeContext
from app.agent_runtime.rules import apply_new_repair_defaults
from app.agent_runtime.runtime_state import RuntimeGraphState, dump_state, load_state


async def interpret_message(
    context: NodeContext, graph_state: RuntimeGraphState
) -> RuntimeGraphState:
    state = load_state(graph_state)
    if not state.property_context_verified or state.current_user_message is None:
        return graph_state
    # The current turn is already carried by ``current_user_message``.  The
    # orchestrator durably appends it before graph invocation, so including the
    # same turn here would make deterministic intent rules misclassify a new
    # repair as conversational follow-up information.
    recent = tuple(
        ConversationMessage(role=item.role, content=item.content)
        for item in state.conversation_messages[-13:]
        if item.turn_id != state.trace_id
    )
    recent = recent[-12:]
    result = await context.interpret(
        InterpretMessageInput(
            current_user_message=state.current_user_message,
            recent_conversation_messages=recent,
            current_state_summary=AgentStateSummary(
                active_ticket_id=state.active_ticket_id,
                active_appointment_id=state.active_appointment_id,
                task_intent=state.task_intent,
                utterance_intent=state.utterance_intent,
                intent_version=state.intent_version,
            ),
            current_workflow_stage=state.workflow_stage,
            known_issue_fields=KnownIssueFields(
                issue_category=state.issue_category,
                issue_location=state.issue_location,
                normalized_issue_location=state.normalized_issue_location,
                issue_description=state.issue_description,
                severity=state.severity,
            ),
            missing_fields=state.missing_fields,
            reference_time=cast(datetime, state.current_reference_time),
            timezone_name=cast(str, state.current_timezone_name),
        )
    )
    merged = apply_new_repair_defaults(merge_interpretation(state, result.interpretation))
    return dump_state(route_required_safety_review(merged))

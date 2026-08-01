"""Bounded response and terminal-review nodes."""

from datetime import UTC, datetime

from app.agent.enums import LLMRole, PendingAction
from app.agent.models import (
    AllowedPolicyEvidence,
    ComposeResponseInput,
    SafeErrorInformation,
    VerifiedBusinessFact,
)
from app.agent_runtime.context import NodeContext
from app.agent_runtime.runtime_state import (
    RuntimeGraphState,
    append_message,
    dump_state,
    finish_with_assistant_message,
    load_state,
)
from app.domain.enums import WorkflowStage


async def compose(context: NodeContext, graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    facts: list[VerifiedBusinessFact] = []
    if state.cached_ticket_snapshot:
        snapshot = state.cached_ticket_snapshot
        facts.append(
            VerifiedBusinessFact(
                fact_id=snapshot.ticket_id,
                fact_type="ticket_snapshot",
                statement=(
                    f"工单状态为 {snapshot.ticket_status.value}，版本 {snapshot.ticket_version}。"
                ),
            )
        )
        if snapshot.active_appointment:
            appointment = snapshot.active_appointment
            facts.append(
                VerifiedBusinessFact(
                    fact_id=appointment.appointment_id,
                    fact_type="active_appointment",
                    statement=(
                        f"预约时间为 {appointment.scheduled_start.isoformat()} 至 "
                        f"{appointment.scheduled_end.isoformat()}。"
                    ),
                )
            )
    safe_error = (
        SafeErrorInformation(
            code=state.escalation_reason,
            message=state.last_assistant_message or "需要人工处理。",
        )
        if state.escalation_reason
        else None
    )
    result = await context.compose(
        ComposeResponseInput(
            verified_business_facts=tuple(facts),
            allowed_policy_evidence=tuple(
                AllowedPolicyEvidence(evidence_id=item, statement="有效政策证据已验证。")
                for item in state.policy_evidence_ids
            ),
            current_workflow_stage=state.workflow_stage,
            task_intent=state.task_intent,
            required_user_action=None,
            safe_error_information=safe_error,
        )
    )
    state = state.model_copy(
        update={
            "last_assistant_message": result.response_text,
            "workflow_stage": (
                WorkflowStage.DONE
                if state.workflow_stage is not WorkflowStage.HUMAN_REVIEW
                else state.workflow_stage
            ),
        }
    )
    return dump_state(
        append_message(
            state,
            role=LLMRole.ASSISTANT,
            content=result.response_text,
            turn_id=state.trace_id,
            created_at=datetime.now(UTC),
        )
    )


async def finish_safety(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    # Safety always stops the existing or new conversation.  It deliberately
    # does not call escalate_to_operator: a trustworthy active ticket may not
    # exist, and Task 8 has no rule that auto-escalates it.
    return dump_state(
        finish_with_assistant_message(
            state,
            message="检测到安全风险，需要紧急人工处理；尚未自动变更工单或预约。",
            updates={
                "workflow_stage": WorkflowStage.EMERGENCY_REVIEW,
                "escalation_reason": "SAFETY_REVIEW_REQUIRED",
            },
        )
    )


async def finish_policy_review(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    return dump_state(
        finish_with_assistant_message(
            state,
            message="政策证据不足或存在冲突，需要人工复核；尚未创建工单。",
            updates={
                "workflow_stage": WorkflowStage.HUMAN_REVIEW,
                "escalation_reason": "POLICY_REVIEW_REQUIRED",
            },
        )
    )


async def finish_manual_request(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    """Resident requests do not mutate a ticket or create an operator action."""

    state = load_state(graph_state)
    message = "该请求需要物业工作人员人工处理。"
    return dump_state(
        finish_with_assistant_message(
            state,
            message=message,
            updates={
                "workflow_stage": WorkflowStage.HUMAN_REVIEW,
                "escalation_reason": "RESIDENT_MANUAL_REQUEST",
                "pending_action": PendingAction.NONE,
                "pending_operation": None,
            },
        )
    )


async def finish_unsupported(graph_state: RuntimeGraphState) -> RuntimeGraphState:
    state = load_state(graph_state)
    return dump_state(
        finish_with_assistant_message(
            state,
            message="该操作当前不能自动执行，已转为人工处理。",
            updates={
                "workflow_stage": WorkflowStage.HUMAN_REVIEW,
                "escalation_reason": "UNSUPPORTED_AUTOMATION",
            },
        )
    )

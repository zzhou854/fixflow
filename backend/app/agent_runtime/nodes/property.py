"""Property-authorisation node."""

from app.agent_runtime.context import NodeContext
from app.agent_runtime.runtime_state import (
    RuntimeGraphState,
    dump_state,
    finish_with_assistant_message,
    load_state,
    require_data,
)
from app.domain.enums import WorkflowStage
from app.property_operations.contracts.common import ResultCode
from app.property_operations.contracts.properties import GetResidentPropertyRequest


async def resolve_property(
    context: NodeContext, graph_state: RuntimeGraphState
) -> RuntimeGraphState:
    state = load_state(graph_state)
    if state.property_context_verified:
        return graph_state
    if state.property_id is None:
        return dump_state(
            finish_with_assistant_message(
                state,
                message="缺少可信房屋上下文，请从已认证入口重新选择房屋。",
                updates={
                    "workflow_stage": WorkflowStage.NEED_PROPERTY,
                },
            )
        )
    response = await context.mcp.get_resident_property(
        GetResidentPropertyRequest(
            actor_type=state.actor_type,
            actor_id=state.actor_id,
            trace_id=state.trace_id,
            resident_id=state.user_id,
            property_id=state.property_id,
        )
    )
    if response.result_code is ResultCode.PERMISSION_DENIED:
        return dump_state(
            finish_with_assistant_message(
                state,
                message="当前身份无权访问该房屋。",
                updates={
                    "workflow_stage": WorkflowStage.HUMAN_REVIEW,
                    "escalation_reason": "PERMISSION_DENIED",
                },
            )
        )
    property_data = require_data(response)
    if property_data.resident_id != state.user_id or property_data.property_id != state.property_id:
        return dump_state(
            finish_with_assistant_message(
                state,
                message="房屋授权响应与请求上下文不一致。",
                updates={
                    "workflow_stage": WorkflowStage.HUMAN_REVIEW,
                    "escalation_reason": "PERMISSION_DENIED",
                },
            )
        )
    return dump_state(state.model_copy(update={"property_context_verified": True}))

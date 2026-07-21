"""Small JSON-state helpers shared by deterministic LangGraph nodes."""

from datetime import datetime
from typing import TypedDict
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel

from app.agent.enums import LLMRole
from app.agent.state import AgentConversationMessage, AgentState
from app.agent_runtime.errors import MCPToolResultError
from app.policy.normalization import stable_json_hash
from app.property_operations.contracts.common import ResultCode, ToolResponse


class RuntimeGraphState(TypedDict):
    """The only checkpointed graph channel: validated AgentState JSON."""

    state_json: str


def load_state(graph_state: RuntimeGraphState) -> AgentState:
    return AgentState.model_validate_json(graph_state["state_json"])


def dump_state(state: AgentState) -> RuntimeGraphState:
    return {"state_json": state.model_dump_json()}


def require_data[DataT: BaseModel](response: ToolResponse[DataT]) -> DataT:
    if response.result_code not in {ResultCode.FOUND, ResultCode.CREATED, ResultCode.UPDATED}:
        code = response.error.code if response.error else response.result_code.value
        raise MCPToolResultError(code)
    if response.data is None:
        raise MCPToolResultError("MCP_CONTRACT_VIOLATION")
    return response.data


def fingerprint(items: object) -> str:
    return stable_json_hash(items)


def append_message(
    state: AgentState, *, role: LLMRole, content: str, turn_id: UUID, created_at: datetime
) -> AgentState:
    """Append exactly once for a logical turn; retries reuse the deterministic ID."""

    message_id = uuid5(NAMESPACE_URL, f"fixflow:{state.thread_id}:{turn_id}:{role.value}")
    if any(item.message_id == message_id for item in state.conversation_messages):
        return state
    messages = (
        *state.conversation_messages,
        AgentConversationMessage(
            message_id=message_id,
            role=role,
            content=content,
            created_at=created_at,
            turn_id=turn_id,
        ),
    )[-100:]
    return state.model_copy(update={"conversation_messages": messages})

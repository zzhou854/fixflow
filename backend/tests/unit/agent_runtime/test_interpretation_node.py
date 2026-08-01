from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import pytest
from app.agent.enums import AgentIntent, LLMRole
from app.agent.models import (
    InterpretationNodeResult,
    InterpretMessageInput,
    InterpretMessageOutput,
    NodeMetadata,
)
from app.agent.state import AgentConversationMessage, AgentState
from app.agent_runtime.context import NodeContext
from app.agent_runtime.nodes.interpretation import interpret_message
from app.agent_runtime.runtime_state import dump_state
from app.domain.enums import ActorType


@pytest.mark.asyncio
async def test_interpretation_excludes_current_turn_from_recent_context() -> None:
    current_trace = uuid4()
    previous_trace = uuid4()
    captured: list[InterpretMessageInput] = []

    async def interpret(node_input: InterpretMessageInput) -> InterpretationNodeResult:
        captured.append(node_input)
        return InterpretationNodeResult(
            interpretation=InterpretMessageOutput(utterance_intent=AgentIntent.UNKNOWN),
            metadata=NodeMetadata(
                provider="test",
                model="test",
                prompt_name="test",
                prompt_version="1",
            ),
        )

    state = AgentState(
        thread_id=uuid4(),
        trace_id=current_trace,
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        user_id=uuid4(),
        property_id=uuid4(),
        property_context_verified=True,
        current_user_message="current repair request",
        current_reference_time=datetime.now(UTC),
        current_timezone_name="UTC",
        conversation_messages=(
            AgentConversationMessage(
                message_id=uuid4(),
                role=LLMRole.USER,
                content="previous question",
                created_at=datetime.now(UTC),
                turn_id=previous_trace,
            ),
            AgentConversationMessage(
                message_id=uuid4(),
                role=LLMRole.ASSISTANT,
                content="previous answer",
                created_at=datetime.now(UTC),
                turn_id=previous_trace,
            ),
            AgentConversationMessage(
                message_id=uuid4(),
                role=LLMRole.USER,
                content="current repair request",
                created_at=datetime.now(UTC),
                turn_id=current_trace,
            ),
        ),
    )
    context = NodeContext(
        mcp=cast(Any, None),
        interpret=interpret,
        compose=cast(Any, None),
        retrieve_policy=cast(Any, None),
    )

    await interpret_message(context, dump_state(state))

    assert len(captured) == 1
    assert captured[0].current_user_message == "current repair request"
    assert [item.content for item in captured[0].recent_conversation_messages] == [
        "previous question",
        "previous answer",
    ]

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.agent.enums import AgentIntent, PendingAction
from app.agent.state import AgentConversationMessage, AgentState
from app.domain.enums import ActorType, WorkflowStage
from app.replay.models import MessageReplayInput, ReplayMessageMetadata
from app.replay.state import project_agent_state, restore_agent_state, state_fingerprint
from pydantic import ValidationError


def _state() -> AgentState:
    now = datetime(2032, 1, 1, 8, tzinfo=UTC)
    return AgentState(
        thread_id=uuid4(),
        trace_id=uuid4(),
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        user_id=uuid4(),
        property_id=uuid4(),
        property_context_verified=True,
        task_intent=AgentIntent.UNKNOWN,
        utterance_intent=AgentIntent.UNKNOWN,
        missing_fields=(),
        workflow_stage=WorkflowStage.INTAKE,
        pending_action=PendingAction.NONE,
        conversation_messages=(
            AgentConversationMessage(
                message_id=uuid4(),
                role="USER",
                content="厨房水管漏水，请明天下午来修",
                created_at=now,
                turn_id=uuid4(),
            ),
        ),
        current_user_message="厨房水管漏水，请明天下午来修",
        current_reference_time=now,
        current_timezone_name="Asia/Shanghai",
    )


def test_replay_input_is_strict_and_does_not_accept_identity_override() -> None:
    metadata = ReplayMessageMetadata(
        message_id=uuid4(),
        content_hash="a" * 64,
        content_length=4,
        language="zh-CN",
        message_role="USER",
    )
    with pytest.raises(ValidationError):
        MessageReplayInput.model_validate(
            {
                "message": metadata.model_dump(mode="json"),
                "reference_time": datetime.now(UTC).isoformat(),
                "timezone_name": "UTC",
                "actor_id": str(uuid4()),
            }
        )


def test_projection_keeps_only_message_metadata_and_redacts_restore() -> None:
    original = _state()
    safe = project_agent_state(original)
    payload = safe.model_dump_json()
    assert "厨房水管漏水" not in payload
    assert safe.conversation_metadata[0].content_hash != "a" * 64
    restored = restore_agent_state(safe)
    assert restored.current_user_message == "[REDACTED FOR REPLAY]"
    assert restored.conversation_messages[0].content == "[REDACTED FOR REPLAY]"


def test_state_fingerprint_excludes_trace_and_message_metadata() -> None:
    safe = project_agent_state(_state())
    changed = safe.model_copy(
        update={
            "trace_id": uuid4(),
            "conversation_metadata": (),
            "run_status": "COMPLETED",
        }
    )
    assert state_fingerprint(safe) == state_fingerprint(changed)


def test_state_fingerprint_detects_business_control_change() -> None:
    safe = project_agent_state(_state())
    changed = safe.model_copy(update={"workflow_stage": WorkflowStage.HUMAN_REVIEW})
    assert state_fingerprint(safe) != state_fingerprint(changed)

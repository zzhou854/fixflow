from datetime import datetime
from zoneinfo import ZoneInfo

from app.agent.enums import IssueField, LLMRole
from app.agent.models import (
    AgentStateSummary,
    ConversationMessage,
    InterpretMessageInput,
    KnownIssueFields,
)
from app.domain.enums import WorkflowStage
from app.llm.sanitizer import (
    InterpretationInputLimits,
    build_sanitized_interpretation_input,
)


def _request() -> InterpretMessageInput:
    return InterpretMessageInput(
        current_user_message="厨房\x00漏水\u0001，请处理",
        recent_conversation_messages=tuple(
            ConversationMessage(role=LLMRole.USER, content=f"历史 {index} " + "字" * 20)
            for index in range(10)
        ),
        current_state_summary=AgentStateSummary(intent_version=2),
        current_workflow_stage=WorkflowStage.INTAKE,
        known_issue_fields=KnownIssueFields(),
        missing_fields=(IssueField.ISSUE_CATEGORY,),
        reference_time=datetime(2032, 1, 1, 9, tzinfo=ZoneInfo("Asia/Shanghai")),
        timezone_name="Asia/Shanghai",
    )


def test_sanitizer_removes_controls_and_stably_bounds_projection() -> None:
    limits = InterpretationInputLimits(
        max_input_characters=80,
        max_context_messages=3,
        max_message_characters=30,
    )
    first = build_sanitized_interpretation_input(_request(), limits)
    second = build_sanitized_interpretation_input(_request(), limits)
    assert "\x00" not in first.payload_json
    assert "\u0001" not in first.payload_json
    assert first.context_message_count == 3
    assert first.input_character_count <= 80
    assert first.input_hash == second.input_hash
    assert "历史 9" in first.payload_json
    assert "历史 0" not in first.payload_json
    assert "authorization" not in first.payload_json.casefold()

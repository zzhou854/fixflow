from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from app.agent.enums import AgentIntent
from app.agent.models import (
    AgentStateSummary,
    InterpretationNodeResult,
    InterpretMessageInput,
    InterpretMessageOutput,
    KnownIssueFields,
    NodeMetadata,
)
from app.domain.enums import WorkflowStage
from app.replay.enums import ReplayStepKind
from app.replay.recorded import RecordedInterpretationNode
from app.replay.steps import InterpretationResultStep, ReplayStepRecord
from app.replay.tape import ReplayTapeCursor

HASH = "a" * 64


def _result() -> InterpretationNodeResult:
    return InterpretationNodeResult(
        interpretation=InterpretMessageOutput(utterance_intent=AgentIntent.UNKNOWN),
        metadata=NodeMetadata(
            provider="scripted",
            model="scripted",
            prompt_name="interpret_message",
            prompt_version="v1",
        ),
    )


def _request() -> InterpretMessageInput:
    return InterpretMessageInput(
        current_user_message="你好",
        recent_conversation_messages=(),
        current_state_summary=AgentStateSummary(intent_version=1),
        current_workflow_stage=WorkflowStage.INTAKE,
        known_issue_fields=KnownIssueFields(),
        missing_fields=(),
        reference_time=datetime(2032, 1, 1, tzinfo=ZoneInfo("UTC")),
        timezone_name="UTC",
    )


def test_old_scripted_interpretation_step_remains_readable() -> None:
    old = InterpretationResultStep.model_validate(
        {
            "provider_type": "SCRIPTED",
            "schema_version": 1,
            "result": _result().model_dump(mode="json"),
            "validation_status": "VALIDATED",
            "input_content_hash": HASH,
        }
    )
    assert old.provider is None
    assert old.prompt_hash is None


@pytest.mark.asyncio
async def test_recorded_interpretation_uses_tape_and_never_needs_glm() -> None:
    step = InterpretationResultStep(
        provider_type="GLM",
        schema_version=1,
        result=_result(),
        input_content_hash=HASH,
        provider="zai",
        model="glm-5.1",
        prompt_id="resident_interpretation",
        prompt_version="1.0.0",
        prompt_hash="b" * 64,
        interpretation_schema_version="interpretation-result-v1",
        thinking_mode="disabled",
    )
    cursor = ReplayTapeCursor(
        (
            ReplayStepRecord(
                sequence_number=1,
                step_kind=ReplayStepKind.INTERPRETATION_RESULT,
                step_key="interpretation:1",
                request_fingerprint=HASH,
                response_schema="InterpretationResultStep",
                payload=step,
                step_checksum="c" * 64,
            ),
        )
    )
    result = await RecordedInterpretationNode(cursor, HASH)(_request())
    assert result == _result()
    assert cursor.remaining == 0

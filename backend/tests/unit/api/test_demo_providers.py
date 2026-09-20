import json

import pytest
from app.agent.enums import AgentIntent, LLMRole
from app.agent.models import InterpretMessageOutput, LLMMessage, LLMRequestConfig
from app.api.demo_providers import DemoScriptedLLMProvider


def test_scripted_provider_recognizes_study_as_issue_location() -> None:
    assert DemoScriptedLLMProvider._location("书房墙上的插座又不灵了") == "书房"


@pytest.mark.parametrize("location", ("主卧", "次卧", "客卧", "小卧室", "卧室"))
def test_scripted_provider_accepts_bedroom_level_location(location: str) -> None:
    assert DemoScriptedLLMProvider._location(location) == "卧室"


@pytest.mark.parametrize(
    ("message", "expected_start", "expected_end"),
    [
        ("下午四点", "2026-09-09T16:00:00+08:00", "2026-09-09T18:00:00+08:00"),
        ("明天下午4点", "2026-09-10T16:00:00+08:00", "2026-09-10T18:00:00+08:00"),
        ("后天下午四点半", "2026-09-11T16:30:00+08:00", "2026-09-11T18:30:00+08:00"),
        ("明天上午十点", "2026-09-10T10:00:00+08:00", "2026-09-10T12:00:00+08:00"),
        ("11号下午2点", "2026-09-11T14:00:00+08:00", "2026-09-11T16:00:00+08:00"),
        ("9月12日下午四点", "2026-09-12T16:00:00+08:00", "2026-09-12T18:00:00+08:00"),
        ("今天下午2点", "2026-09-09T14:00:00+08:00", "2026-09-09T16:00:00+08:00"),
        ("11号16:30", "2026-09-11T16:30:00+08:00", "2026-09-11T18:30:00+08:00"),
        ("11号下午2点到5点", "2026-09-11T14:00:00+08:00", "2026-09-11T17:00:00+08:00"),
        ("明天下午两点半至五点半", "2026-09-10T14:30:00+08:00", "2026-09-10T17:30:00+08:00"),
    ],
)
def test_scripted_provider_respects_explicit_time_as_earliest_slot(
    message: str,
    expected_start: str,
    expected_end: str,
) -> None:
    result = DemoScriptedLLMProvider._availability(
        message,
        {
            "reference_time": "2026-09-09T22:00:00+08:00",
            "timezone_name": "Asia/Shanghai",
        },
    )

    assert result == {"starts_at": expected_start, "ends_at": expected_end}


@pytest.mark.parametrize(
    "message",
    ("厨房有点漏水", "9月31号下午2点", "明天下午25点", "明天下午5点到2点"),
)
def test_scripted_provider_does_not_invent_availability_from_invalid_or_non_time_text(
    message: str,
) -> None:
    result = DemoScriptedLLMProvider._availability(
        message,
        {
            "reference_time": "2026-09-09T22:00:00+08:00",
            "timezone_name": "Asia/Shanghai",
        },
    )

    assert result is None


def test_bare_clock_stays_on_today_so_the_workflow_can_reject_it_as_past() -> None:
    result = DemoScriptedLLMProvider._availability(
        "下午两点",
        {
            "reference_time": "2026-09-10T15:30:00+08:00",
            "timezone_name": "Asia/Shanghai",
        },
    )

    assert result == {
        "starts_at": "2026-09-10T14:00:00+08:00",
        "ends_at": "2026-09-10T16:00:00+08:00",
    }


def test_explicit_tomorrow_clock_remains_tomorrow() -> None:
    result = DemoScriptedLLMProvider._availability(
        "明天下午两点",
        {
            "reference_time": "2026-09-10T15:30:00+08:00",
            "timezone_name": "Asia/Shanghai",
        },
    )

    assert result is not None
    assert result["starts_at"] == "2026-09-11T14:00:00+08:00"


@pytest.mark.asyncio
async def test_pure_availability_does_not_replace_existing_issue_description() -> None:
    provider = DemoScriptedLLMProvider()
    context = {
        "current_message": "11号下午2点",
        "current_task_intent": "NEW_REPAIR",
        "known_issue_fields": {
            "issue_category": "DOOR_LOCK",
            "issue_description": "入户门锁打不开",
        },
        "reference_time": "2026-09-10T00:15:00+08:00",
        "timezone_name": "Asia/Shanghai",
    }

    result = await provider.generate_structured(
        messages=(LLMMessage(role=LLMRole.USER, content=f"context: {json.dumps(context)}"),),
        response_model=InterpretMessageOutput,
        model_config=LLMRequestConfig(
            model="scripted",
            prompt_name="test",
            prompt_version="1",
            max_output_tokens=100,
        ),
    )

    assert "issue_description_update" not in result.payload
    assert result.payload["user_availability_windows"] == [
        {
            "starts_at": "2026-09-11T14:00:00+08:00",
            "ends_at": "2026-09-11T16:00:00+08:00",
        }
    ]


@pytest.mark.asyncio
async def test_scripted_provider_keeps_flat_sanitized_task_context_for_follow_up() -> None:
    provider = DemoScriptedLLMProvider()
    context = {
        "current_message": "明天下午三点",
        "current_task_intent": "RESCHEDULE_APPOINTMENT",
        "known_issue_fields": {"issue_category": "ELECTRICAL"},
        "reference_time": "2026-09-09T14:00:00+08:00",
        "timezone_name": "Asia/Shanghai",
    }

    result = await provider.generate_structured(
        messages=(
            LLMMessage(role=LLMRole.USER, content=f"context: {json.dumps(context)}"),
        ),
        response_model=InterpretMessageOutput,
        model_config=LLMRequestConfig(
            model="scripted",
            prompt_name="test",
            prompt_version="1",
            max_output_tokens=100,
        ),
    )

    assert result.payload["utterance_intent"] == AgentIntent.PROVIDE_INFORMATION.value

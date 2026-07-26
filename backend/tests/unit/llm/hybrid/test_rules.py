from __future__ import annotations

from datetime import datetime

import pytest
from app.agent.enums import AgentIntent, IssueField, SafetyFlag
from app.agent.models import (
    AgentStateSummary,
    ConversationMessage,
    InterpretMessageInput,
    KnownIssueFields,
)
from app.domain.enums import IssueCategory, WorkflowStage
from app.llm.hybrid.decision import InterpretationConflictDetector, ResidentIntentDecisionEngine
from app.llm.hybrid.models import HybridDecision, NormalizedResidentFactsV1
from app.llm.hybrid.normalizer import ResidentFactNormalizer

from tests.unit.llm.hybrid.conftest import empty_facts


def node_input(
    message: str,
    *,
    task: AgentIntent = AgentIntent.UNKNOWN,
    recent: tuple[ConversationMessage, ...] = (),
    known: KnownIssueFields | None = None,
) -> InterpretMessageInput:
    return InterpretMessageInput(
        current_user_message=message,
        recent_conversation_messages=recent,
        current_state_summary=AgentStateSummary(task_intent=task, intent_version=1),
        current_workflow_stage=WorkflowStage.INTAKE,
        known_issue_fields=known or KnownIssueFields(),
        missing_fields=(),
        reference_time=datetime.fromisoformat("2026-07-23T09:00:00+08:00"),
        timezone_name="Asia/Shanghai",
    )


def decide(
    message: str,
    *,
    task: AgentIntent = AgentIntent.UNKNOWN,
) -> tuple[NormalizedResidentFactsV1, HybridDecision]:
    source = node_input(message, task=task)
    facts = ResidentFactNormalizer().normalize(
        empty_facts(),
        current_user_message=message,
    )
    return facts, ResidentIntentDecisionEngine().decide(facts, node_input=source)


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("厨房水管漏水，请安排维修。", AgentIntent.NEW_REPAIR),
        ("我要转人工处理这个漏水问题。", AgentIntent.REQUEST_HUMAN),
        ("请把原预约改到周五下午。", AgentIntent.RESCHEDULE_APPOINTMENT),
        ("工单已经有了，我想预约师傅上门。", AgentIntent.SELECT_APPOINTMENT_SLOT),
        ("嗨，早上好。", AgentIntent.UNKNOWN),
        ("帮我代缴本月水电费。", AgentIntent.UNKNOWN),
    ],
)
def test_intent_priority_is_deterministic(message: str, intent: AgentIntent) -> None:
    first = decide(message)[1]
    second = decide(message)[1]
    assert first == second
    assert first.utterance_intent is intent


def test_missing_fields_and_clarification_are_one_deterministic_decision() -> None:
    _, decision = decide("一直漏水。")
    assert decision.missing_fields == (IssueField.ISSUE_LOCATION,)
    assert decision.clarification_needed is True

    _, human = decide("我要找人工。")
    assert human.missing_fields == ()
    assert human.clarification_needed is False


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("没有闻到燃气味，只是普通咨询。", ()),
        (
            "浴室积水碰到插线板，有触电危险。",
            (
                SafetyFlag.ACTIVE_FLOODING,
                SafetyFlag.ELECTRICAL_HAZARD,
                SafetyFlag.IMMEDIATE_DANGER,
            ),
        ),
        (
            "孩子被反锁在房间里。",
            (SafetyFlag.IMMEDIATE_DANGER, SafetyFlag.LOCKOUT_RISK),
        ),
        ("如果以后冒烟怎么办？", ()),
    ],
)
def test_safety_handles_negation_hypothetical_and_multiple_labels(
    message: str,
    expected: tuple[SafetyFlag, ...],
) -> None:
    assert decide(message)[1].safety_flags == expected


def test_conflict_detector_is_read_only() -> None:
    facts, decision = decide("我没有现有预约，但想说改期。")
    before = facts.model_dump_json()
    conflicts = InterpretationConflictDetector().detect(facts, decision)
    assert conflicts
    assert facts.model_dump_json() == before


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("请让真人物业接手这个问题。", AgentIntent.REQUEST_HUMAN),
        ("已约的上门时间改成明晚。", AgentIntent.RESCHEDULE_APPOINTMENT),
        ("维修人员来的日期需要重新约。", AgentIntent.RESCHEDULE_APPOINTMENT),
        ("后天下午两点到五点我都方便。", AgentIntent.PROVIDE_INFORMATION),
    ],
)
def test_historical_holdout_language_is_covered_deterministically(
    message: str,
    intent: AgentIntent,
) -> None:
    assert decide(message)[1].utterance_intent is intent


def test_slot_selection_uses_bounded_recent_assistant_context() -> None:
    message = "我选第一个。"
    source = node_input(
        message,
        recent=(
            ConversationMessage(
                role="ASSISTANT",
                content="系统已给出两个可选上门时间。",
            ),
        ),
    )
    facts = ResidentFactNormalizer().normalize(
        empty_facts(),
        current_user_message=message,
    )

    decision = ResidentIntentDecisionEngine().decide(facts, node_input=source)

    assert decision.utterance_intent is AgentIntent.SELECT_APPOINTMENT_SLOT
    assert decision.missing_fields == ()


def test_active_repair_context_controls_supplement_requirements() -> None:
    message = "就是阳台那里。"
    source = node_input(
        message,
        task=AgentIntent.NEW_REPAIR,
        known=KnownIssueFields(
            issue_category=IssueCategory.WATER_LEAK,
            issue_location="阳台",
            normalized_issue_location="阳台",
        ),
    )
    facts = ResidentFactNormalizer().normalize(
        empty_facts(),
        current_user_message=message,
    )

    decision = ResidentIntentDecisionEngine().decide(facts, node_input=source)

    assert decision.utterance_intent is AgentIntent.PROVIDE_INFORMATION
    assert decision.missing_fields == (IssueField.ISSUE_DESCRIPTION,)


@pytest.mark.parametrize(
    ("message", "flags"),
    [
        ("电表箱冒烟了。", (SafetyFlag.ELECTRICAL_HAZARD, SafetyFlag.IMMEDIATE_DANGER)),
        (
            "水管爆了，水流到通电插排旁边。",
            (
                SafetyFlag.ACTIVE_FLOODING,
                SafetyFlag.ELECTRICAL_HAZARD,
                SafetyFlag.IMMEDIATE_DANGER,
            ),
        ),
        (
            "老人被反锁在卫生间。",
            (SafetyFlag.IMMEDIATE_DANGER, SafetyFlag.LOCKOUT_RISK),
        ),
    ],
)
def test_extended_safety_language_maps_to_frozen_flags(
    message: str,
    flags: tuple[SafetyFlag, ...],
) -> None:
    assert decide(message)[1].safety_flags == flags


def test_negated_model_safety_evidence_cannot_override_source_context() -> None:
    facts = ResidentFactNormalizer().normalize(
        empty_facts(),
        current_user_message="插座没有火花也没有焦味，只是没电。",
    )
    decision = ResidentIntentDecisionEngine().decide(
        facts,
        node_input=node_input(facts.source_text),
    )
    assert decision.safety_flags == ()

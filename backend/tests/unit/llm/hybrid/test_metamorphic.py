from __future__ import annotations

from dataclasses import dataclass

import pytest
from app.agent.enums import AgentIntent, IssueField, SafetyFlag
from app.domain.enums import IssueCategory
from app.llm.hybrid.decision import ResidentIntentDecisionEngine, resolved_issue_category
from app.llm.hybrid.normalizer import ResidentFactNormalizer

from tests.unit.llm.hybrid.conftest import empty_facts
from tests.unit.llm.hybrid.test_rules import node_input


@dataclass(frozen=True, slots=True)
class MetamorphicExpectation:
    message: str
    intent: AgentIntent
    category: IssueCategory | None = None
    missing: tuple[IssueField, ...] | None = None
    required_safety: tuple[SafetyFlag, ...] = ()


_BASES = (
    MetamorphicExpectation(
        "厨房水管正在漏水，需要维修。",
        AgentIntent.NEW_REPAIR,
        IssueCategory.WATER_LEAK,
    ),
    MetamorphicExpectation(
        "客厅墙插没有电，需要检修。",
        AgentIntent.NEW_REPAIR,
        IssueCategory.ELECTRICAL,
    ),
    MetamorphicExpectation(
        "卧室门锁打不开，需要维修。",
        AgentIntent.NEW_REPAIR,
        IssueCategory.DOOR_LOCK,
    ),
    MetamorphicExpectation(
        "请转人工处理厨房漏水。",
        AgentIntent.REQUEST_HUMAN,
        IssueCategory.WATER_LEAK,
        (),
    ),
    MetamorphicExpectation(
        "原预约改到周五下午。",
        AgentIntent.RESCHEDULE_APPOINTMENT,
        None,
        (),
    ),
    MetamorphicExpectation(
        "原预约需要改期，新时间还没定。",
        AgentIntent.RESCHEDULE_APPOINTMENT,
        None,
        (IssueField.AVAILABILITY,),
    ),
    MetamorphicExpectation(
        "已有工单，请安排师傅上门。",
        AgentIntent.SELECT_APPOINTMENT_SLOT,
        None,
        None,
    ),
    MetamorphicExpectation("晚上好，聊聊天吧。", AgentIntent.UNKNOWN, None, ()),
    MetamorphicExpectation("帮我订一份早餐。", AgentIntent.UNKNOWN, None, ()),
    MetamorphicExpectation(
        "卫生间积水碰到通电的洗衣机。",
        AgentIntent.NEW_REPAIR,
        IssueCategory.WATER_LEAK,
        (),
        (
            SafetyFlag.ELECTRICAL_HAZARD,
            SafetyFlag.IMMEDIATE_DANGER,
        ),
    ),
)

_PREFIXES = ("", "你好，", "麻烦你听一下，", "当前情况：", "我说明一下：")
_SUFFIXES = (
    "",
    "谢谢。",
    "以上情况属实。",
    "请记录一下。",
    "这是当前情况。",
    "请确认收到。",
    "辛苦了。",
    "情况就是这样。",
    "以上。",
    "先这样。",
)


def _cases() -> tuple[MetamorphicExpectation, ...]:
    return tuple(
        MetamorphicExpectation(
            message=f"{prefix}{base.message}{suffix}",
            intent=base.intent,
            category=base.category,
            missing=base.missing,
            required_safety=base.required_safety,
        )
        for base in _BASES
        for prefix in _PREFIXES
        for suffix in _SUFFIXES
    )


@pytest.mark.parametrize("case", _cases())
def test_semantic_metamorphic_regression(case: MetamorphicExpectation) -> None:
    """500 deterministic, PII-free composition cases exercise the control plane."""

    source = node_input(case.message)
    facts = ResidentFactNormalizer().normalize(
        empty_facts(),
        current_user_message=case.message,
    )
    decision = ResidentIntentDecisionEngine().decide(facts, node_input=source)

    assert decision.utterance_intent is case.intent
    assert resolved_issue_category(facts) is case.category
    if case.missing is not None:
        assert decision.missing_fields == case.missing
        assert decision.clarification_needed is bool(case.missing)
    assert set(case.required_safety).issubset(decision.safety_flags)

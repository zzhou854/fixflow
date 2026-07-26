from __future__ import annotations

from datetime import datetime

import pytest
from app.agent.enums import AgentIntent, IssueField, SafetyFlag
from app.agent.models import AgentStateSummary, InterpretMessageInput, KnownIssueFields
from app.domain.enums import WorkflowStage
from app.llm.hybrid.decision import InterpretationConflictDetector, ResidentIntentDecisionEngine
from app.llm.hybrid.models import HybridDecision, NormalizedResidentFactsV1
from app.llm.hybrid.normalizer import ResidentFactNormalizer

from tests.unit.llm.hybrid.conftest import empty_facts


def node_input(message: str, *, task: AgentIntent = AgentIntent.UNKNOWN) -> InterpretMessageInput:
    return InterpretMessageInput(
        current_user_message=message,
        recent_conversation_messages=(),
        current_state_summary=AgentStateSummary(task_intent=task, intent_version=1),
        current_workflow_stage=WorkflowStage.INTAKE,
        known_issue_fields=KnownIssueFields(),
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

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
from app.llm.hybrid.semantic import (
    ResidentSemanticAct,
    derive_semantic_acts,
    derive_semantic_features,
)

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


@pytest.mark.parametrize(
    ("message", "intent", "category", "missing", "safety"),
    [
        (
            "卧室门锁需要报修，具体表现还没确认。",
            AgentIntent.NEW_REPAIR,
            IssueCategory.DOOR_LOCK,
            (IssueField.ISSUE_DESCRIPTION,),
            (),
        ),
        (
            "我要报修阳台水管，具体表现还不清楚。",
            AgentIntent.NEW_REPAIR,
            IssueCategory.WATER_LEAK,
            (IssueField.ISSUE_DESCRIPTION,),
            (),
        ),
        (
            "已有工单，请给我安排师傅上门。",
            AgentIntent.SELECT_APPOINTMENT_SLOT,
            None,
            None,
            (),
        ),
        (
            "已约的师傅来访时间重新安排到明晚。",
            AgentIntent.RESCHEDULE_APPOINTMENT,
            None,
            (),
            (),
        ),
        (
            "洗衣房水管爆裂，水正涌向通电的洗衣机。",
            AgentIntent.NEW_REPAIR,
            IssueCategory.WATER_LEAK,
            (),
            (
                SafetyFlag.ACTIVE_FLOODING,
                SafetyFlag.ELECTRICAL_HAZARD,
                SafetyFlag.IMMEDIATE_DANGER,
            ),
        ),
        (
            "客卫顶部不断灌水，地面积水正在快速扩大。",
            AgentIntent.NEW_REPAIR,
            IssueCategory.WATER_LEAK,
            (),
            (SafetyFlag.ACTIVE_FLOODING, SafetyFlag.IMMEDIATE_DANGER),
        ),
        (
            "书房墙插正在冒烟，并且有烧焦气味。",
            AgentIntent.NEW_REPAIR,
            IssueCategory.ELECTRICAL,
            (),
            (
                SafetyFlag.ELECTRICAL_HAZARD,
                SafetyFlag.IMMEDIATE_DANGER,
            ),
        ),
        (
            "卫生间漏下来的水已经落到亮着的照明灯旁。",
            AgentIntent.NEW_REPAIR,
            IssueCategory.WATER_LEAK,
            (),
            (
                SafetyFlag.ACTIVE_FLOODING,
                SafetyFlag.ELECTRICAL_HAZARD,
                SafetyFlag.IMMEDIATE_DANGER,
            ),
        ),
    ],
)
def test_consumed_challenge_v7_failure_shapes_are_regressions(
    message: str,
    intent: AgentIntent,
    category: IssueCategory | None,
    missing: tuple[IssueField, ...] | None,
    safety: tuple[SafetyFlag, ...],
) -> None:
    facts, decision = decide(message)
    from app.llm.hybrid.decision import resolved_issue_category

    assert decision.utterance_intent is intent
    assert resolved_issue_category(facts) is category
    if missing is not None:
        assert decision.missing_fields == missing
    assert decision.safety_flags == safety


def test_semantic_acts_are_closed_and_correction_precedes_reschedule() -> None:
    message = "更正时间，之前说周二不准确，改到周四上午。"
    source = node_input(message)
    facts = ResidentFactNormalizer().normalize(empty_facts(), current_user_message=message)
    acts = derive_semantic_acts(facts, node_input=source)
    assert acts.contains(ResidentSemanticAct.CORRECT_PREVIOUS_FACT)
    assert acts.contains(ResidentSemanticAct.REQUEST_RESCHEDULE)
    assert (
        ResidentIntentDecisionEngine().decide(facts, node_input=source).utterance_intent
        is AgentIntent.PROVIDE_INFORMATION
    )


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


@pytest.mark.parametrize(
    ("message", "intent", "missing"),
    [
        ("卧室有个设施不能使用了，请检查。", AgentIntent.NEW_REPAIR, (IssueField.ISSUE_CATEGORY,)),
        (
            "已确认的预约时间不合适，要重新安排。",
            AgentIntent.RESCHEDULE_APPOINTMENT,
            (IssueField.AVAILABILITY,),
        ),
        ("就定孙师傅对应的时间段。", AgentIntent.SELECT_APPOINTMENT_SLOT, ()),
        ("星期日全天都可以，维修时长由系统确定。", AgentIntent.PROVIDE_INFORMATION, ()),
        (
            "这次预约向后延，具体日期还没想好。",
            AgentIntent.RESCHEDULE_APPOINTMENT,
            (IssueField.AVAILABILITY,),
        ),
        ("报修不要撤销，只调整师傅上门时间到周三。", AgentIntent.RESCHEDULE_APPOINTMENT, ()),
        ("不要自动安排师傅，让工作人员给我回电。", AgentIntent.REQUEST_HUMAN, ()),
        ("帮我订购一份早餐送到楼下。", AgentIntent.UNKNOWN, ()),
    ],
)
def test_challenge_v6_failure_families_are_generalized(
    message: str,
    intent: AgentIntent,
    missing: tuple[IssueField, ...],
) -> None:
    decision = decide(message)[1]
    assert decision.utterance_intent is intent
    if missing:
        assert set(decision.missing_fields).issuperset(missing)
    else:
        assert decision.missing_fields == ()


@pytest.mark.parametrize(
    ("message", "category"),
    [
        ("书房灯不断闪，请让物业人工处理。", IssueCategory.ELECTRICAL),
        ("客厅灯无法点亮，没有烟、火花、异味或发热。", IssueCategory.ELECTRICAL),
        ("老人困在储藏室，双方都无法开门。", IssueCategory.DOOR_LOCK),
    ],
)
def test_challenge_v6_category_paraphrases_are_normalized(
    message: str,
    category: IssueCategory,
) -> None:
    facts, _ = decide(message)
    from app.llm.hybrid.decision import resolved_issue_category

    assert resolved_issue_category(facts) is category


@pytest.mark.parametrize(
    ("message", "feature"),
    [
        ("请让工作人员给我回电。", "callback_request"),
        ("已确认的预约时间需要重新安排。", "confirmed_appointment_reference"),
        ("原预约向后延，具体日期还没想好。", "availability_missing"),
        ("就定王师傅对应的时间段。", "slot_selection_request"),
        ("这个设施已经无法使用。", "generic_facility_failure"),
        ("不要取消工单，只调整上门时间。", "negated_cancellation"),
        ("维修时长由系统确定，星期日全天都可以。", "system_owned_duration"),
    ],
)
def test_semantic_features_are_compositional(message: str, feature: str) -> None:
    source = node_input(message)
    facts = ResidentFactNormalizer().normalize(
        empty_facts(),
        current_user_message=message,
    )
    features = derive_semantic_features(facts, node_input=source)

    assert getattr(features, feature) is True

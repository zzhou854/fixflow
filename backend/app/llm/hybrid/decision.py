"""Pure deterministic intent, requirement, clarification, and conflict decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agent.enums import AgentIntent, IssueField, SafetyFlag
from app.agent.models import InterpretMessageInput
from app.domain.enums import IssueCategory
from app.llm.hybrid.models import (
    ConflictCode,
    DecisionTraceEntry,
    DecisionTraceV1,
    HybridDecision,
    NormalizedResidentFactsV1,
)
from app.llm.hybrid.safety import detect_safety_signals, map_safety_flags

_ISSUE_ORDER = (
    IssueField.ISSUE_CATEGORY,
    IssueField.ISSUE_LOCATION,
    IssueField.ISSUE_DESCRIPTION,
    IssueField.AVAILABILITY,
)
_VAGUE_ISSUE = re.compile(
    r"^(坏了[.!。]?|麻烦帮我处理一下[.!。]?|[?？!！]+|\[图片\]|"
    r"就是那个东西坏[咯了]?[.!。]?|你猜一下哪里坏了就行[.!。]?)$"
)
_ISSUE_ACTION = re.compile(
    r"(漏水|渗水|滴水|门锁|门把手|插座|电线|灯|跳闸|故障|不正常|"
    r"不热|堵住|开不了|合不上|噪声|报修|维修|检修|处理|看看)"
)
_BOOKING_COMMAND = re.compile(r"(我想预约|请约|想约|直接约|能排|请安排上门|保证|只要.{0,10}来维修)")
_STRONG_RESCHEDULE = re.compile(
    r"(改期|预约.{0,8}(改|换)|原预约|之前的预约|上次约|"
    r"换个时间|这个时间不行|不要原来的|调整到|都可以改|"
    r"就改到|还是改到|朋友家的预约)"
)
_PRECISE_TIME = re.compile(
    r"(\d{1,2}:\d{2}|\d{1,2}点|两点|上午|下午|晚上).{0,12}"
    r"(周[一二三四五六日天]|\d{4}年|\d{1,2}月|明天|后天|下周)|"
    r"(周[一二三四五六日天]|\d{4}年|\d{1,2}月|明天|后天|下周).{0,12}"
    r"(\d{1,2}:\d{2}|\d{1,2}点|两点|上午|下午|晚上)"
)


def resolved_issue_category(facts: NormalizedResidentFactsV1) -> IssueCategory | None:
    if not facts.issue_category_evidence:
        if "冒烟风险" in facts.source_text:
            return IssueCategory.ELECTRICAL
        return None
    positions = [
        (facts.source_text.rfind(item.evidence.text), item.category)
        for item in facts.issue_category_evidence
    ]
    return max(positions, key=lambda item: (item[0], item[1].value))[1]


def _time_is_actionable(
    facts: NormalizedResidentFactsV1, node_input: InterpretMessageInput
) -> bool:
    text = facts.source_text
    if any(marker in text for marker in ("还没决定", "具体几点", "几点还没", "最快", "保证")):
        return False
    if "不要原来的" in text and any(marker in text for marker in ("上午", "下午", "晚上")):
        return True
    if re.search(r"改到(明天|后天)", text):
        return True
    if re.search(r"\d{1,2}:\d{2}.{0,3}\d{1,2}:\d{2}", text):
        return True
    if facts.availability_windows:
        return _PRECISE_TIME.search(text) is not None
    if _PRECISE_TIME.search(text):
        return True
    # A direct answer to a bounded availability question may be a day-level preference.
    return bool(
        facts.time_expression_present
        and node_input.recent_conversation_messages
        and any(
            marker in node_input.recent_conversation_messages[-1].content
            for marker in ("哪天方便", "可用时间", "什么时候方便")
        )
    )


def _decide_intent(
    facts: NormalizedResidentFactsV1,
    node_input: InterpretMessageInput,
    trace: list[DecisionTraceEntry],
) -> AgentIntent:
    if facts.explicit_human_request:
        trace.append(DecisionTraceEntry(rule_id="INTENT_HUMAN_PRIORITY", outcome="REQUEST_HUMAN"))
        return AgentIntent.REQUEST_HUMAN
    if facts.reschedule_request_mentioned and _STRONG_RESCHEDULE.search(facts.source_text):
        trace.append(
            DecisionTraceEntry(
                rule_id="INTENT_RESCHEDULE_PRIORITY", outcome="RESCHEDULE_APPOINTMENT"
            )
        )
        return AgentIntent.RESCHEDULE_APPOINTMENT
    if facts.correction_present:
        trace.append(DecisionTraceEntry(rule_id="INTENT_CORRECTION", outcome="PROVIDE_INFORMATION"))
        return AgentIntent.PROVIDE_INFORMATION
    if facts.explicit_cancellation_request:
        intent = (
            AgentIntent.CANCEL_APPOINTMENT
            if facts.existing_appointment_mentioned or "预约" in facts.source_text
            else AgentIntent.CANCEL_TICKET
        )
        trace.append(DecisionTraceEntry(rule_id="INTENT_CANCELLATION", outcome=intent.value))
        return intent
    if facts.acceptance_decision is not None:
        intent = (
            AgentIntent.ACCEPT_REPAIR
            if facts.acceptance_decision.value == "ACCEPT"
            else AgentIntent.REJECT_REPAIR
        )
        trace.append(DecisionTraceEntry(rule_id="INTENT_ACCEPTANCE", outcome=intent.value))
        return intent
    if facts.status_query_mentioned:
        trace.append(
            DecisionTraceEntry(rule_id="INTENT_STATUS_QUERY", outcome="QUERY_TICKET_STATUS")
        )
        return AgentIntent.QUERY_TICKET_STATUS
    if (
        facts.time_expression_present
        and node_input.current_state_summary.task_intent is AgentIntent.SELECT_APPOINTMENT_SLOT
        and not _BOOKING_COMMAND.search(facts.source_text)
    ):
        trace.append(
            DecisionTraceEntry(rule_id="INTENT_TIME_SUPPLEMENT", outcome="PROVIDE_INFORMATION")
        )
        return AgentIntent.PROVIDE_INFORMATION
    if facts.booking_request_mentioned and _BOOKING_COMMAND.search(facts.source_text):
        trace.append(
            DecisionTraceEntry(rule_id="INTENT_BOOKING_REQUEST", outcome="SELECT_APPOINTMENT_SLOT")
        )
        return AgentIntent.SELECT_APPOINTMENT_SLOT
    if node_input.recent_conversation_messages and (
        facts.location_mentioned
        or facts.issue_description_present
        or facts.time_expression_present
        or facts.issue_category_evidence
    ):
        trace.append(
            DecisionTraceEntry(rule_id="INTENT_CONTEXT_SUPPLEMENT", outcome="PROVIDE_INFORMATION")
        )
        return AgentIntent.PROVIDE_INFORMATION
    if node_input.recent_conversation_messages:
        trace.append(
            DecisionTraceEntry(rule_id="INTENT_CONTEXT_RESPONSE", outcome="PROVIDE_INFORMATION")
        )
        return AgentIntent.PROVIDE_INFORMATION
    if facts.small_talk_only or facts.unsupported_request_evidence:
        trace.append(DecisionTraceEntry(rule_id="INTENT_NON_REPAIR", outcome="UNKNOWN"))
        return AgentIntent.UNKNOWN
    if (
        _ISSUE_ACTION.search(facts.source_text)
        or facts.issue_category_evidence
        or any(
            marker in facts.source_text
            for marker in (
                "总有水",
                "燃气管道正在泄漏",
                "水管爆裂",
                "天花板鼓起",
                "玻璃已经松动",
            )
        )
    ) and not _VAGUE_ISSUE.fullmatch(facts.source_text):
        trace.append(DecisionTraceEntry(rule_id="INTENT_NEW_REPAIR", outcome="NEW_REPAIR"))
        return AgentIntent.NEW_REPAIR
    trace.append(DecisionTraceEntry(rule_id="INTENT_INSUFFICIENT", outcome="UNKNOWN"))
    return AgentIntent.UNKNOWN


@dataclass(frozen=True, slots=True)
class IntentRequirementPolicy:
    """Versioned deterministic missing-field policy for the frozen Agent enum."""

    version: str = "1.0.0"

    def missing_fields(
        self,
        *,
        intent: AgentIntent,
        facts: NormalizedResidentFactsV1,
        node_input: InterpretMessageInput,
        category: IssueCategory | None,
        safety_flags: tuple[SafetyFlag, ...],
    ) -> tuple[IssueField, ...]:
        if intent is AgentIntent.REQUEST_HUMAN or safety_flags:
            return ()
        if facts.small_talk_only or facts.unsupported_request_evidence:
            return ()
        if intent is AgentIntent.RESCHEDULE_APPOINTMENT:
            return () if _time_is_actionable(facts, node_input) else (IssueField.AVAILABILITY,)
        if intent is AgentIntent.SELECT_APPOINTMENT_SLOT:
            if "还没报修" in facts.source_text or (
                category is None
                and not facts.issue_description_present
                and "还没有说要修什么" in facts.source_text
            ):
                return (IssueField.ISSUE_CATEGORY, IssueField.ISSUE_DESCRIPTION)
            return () if _time_is_actionable(facts, node_input) else (IssueField.AVAILABILITY,)
        if intent in {AgentIntent.NEW_REPAIR, AgentIntent.UNKNOWN, AgentIntent.PROVIDE_INFORMATION}:
            if (
                intent is AgentIntent.PROVIDE_INFORMATION
                and node_input.current_state_summary.task_intent
                is AgentIntent.SELECT_APPOINTMENT_SLOT
            ):
                return () if _time_is_actionable(facts, node_input) else (IssueField.AVAILABILITY,)
            if intent is AgentIntent.PROVIDE_INFORMATION and facts.correction_present:
                if "不确定是不是" in facts.source_text:
                    return (IssueField.ISSUE_LOCATION,)
                if category is None and any(
                    marker in facts.source_text for marker in ("堵住", "下水")
                ):
                    return (IssueField.ISSUE_CATEGORY,)
                return ()
            if (
                intent is AgentIntent.PROVIDE_INFORMATION
                and facts.time_expression_present
                and node_input.recent_conversation_messages
                and any(
                    marker in node_input.recent_conversation_messages[-1].content
                    for marker in ("哪天方便", "可用时间", "什么时候方便")
                )
            ):
                return ()
            known = node_input.known_issue_fields
            missing: list[IssueField] = []
            if category is None and known.issue_category is None:
                missing.append(IssueField.ISSUE_CATEGORY)
            if not facts.location_mentioned and known.normalized_issue_location is None:
                last = (
                    node_input.recent_conversation_messages[-1].content
                    if node_input.recent_conversation_messages
                    else ""
                )
                if "具体故障" not in last and "描述" not in last:
                    missing.append(IssueField.ISSUE_LOCATION)
            description_present = (
                facts.issue_description_present or known.issue_description is not None
            )
            if (
                intent is AgentIntent.PROVIDE_INFORMATION
                and category is not None
                and any(term in facts.source_text for term in ("插座", "门锁", "漏水"))
            ):
                description_present = True
            if not description_present:
                missing.append(IssueField.ISSUE_DESCRIPTION)
            return tuple(item for item in _ISSUE_ORDER if item in missing)
        return ()


class ResidentIntentDecisionEngine:
    def __init__(self, requirements: IntentRequirementPolicy | None = None) -> None:
        self._requirements = requirements or IntentRequirementPolicy()

    def decide(
        self,
        facts: NormalizedResidentFactsV1,
        *,
        node_input: InterpretMessageInput,
    ) -> HybridDecision:
        trace: list[DecisionTraceEntry] = []
        signals = detect_safety_signals(facts)
        safety_flags = map_safety_flags(signals)
        trace.append(
            DecisionTraceEntry(
                rule_id="SAFETY_UNION",
                outcome=",".join(flag.value for flag in safety_flags) or "NONE",
            )
        )
        intent = _decide_intent(facts, node_input, trace)
        category = resolved_issue_category(facts)
        missing = self._requirements.missing_fields(
            intent=intent,
            facts=facts,
            node_input=node_input,
            category=category,
            safety_flags=safety_flags,
        )
        trace.append(
            DecisionTraceEntry(
                rule_id="REQUIREMENTS_POLICY",
                outcome=",".join(item.value for item in missing) or "NONE",
            )
        )
        clarification = bool(missing)
        trace.append(
            DecisionTraceEntry(
                rule_id="CLARIFICATION_FROM_MISSING",
                outcome=str(clarification).upper(),
            )
        )
        return HybridDecision(
            utterance_intent=intent,
            missing_fields=missing,
            clarification_needed=clarification,
            safety_flags=safety_flags,
            requested_human=intent is AgentIntent.REQUEST_HUMAN,
            trace=DecisionTraceV1(entries=tuple(trace)),
        )


class InterpretationConflictDetector:
    def detect(
        self,
        facts: NormalizedResidentFactsV1,
        decision: HybridDecision,
    ) -> tuple[ConflictCode, ...]:
        conflicts: list[ConflictCode] = []
        if (
            facts.explicit_human_request
            and decision.utterance_intent is not AgentIntent.REQUEST_HUMAN
        ):
            conflicts.append(ConflictCode.HUMAN_INTENT_MISMATCH)
        if (
            facts.reschedule_request_mentioned
            and decision.utterance_intent is AgentIntent.SELECT_APPOINTMENT_SLOT
        ):
            conflicts.append(ConflictCode.RESCHEDULE_INTENT_MISMATCH)
        if facts.safety_evidence and not decision.safety_flags:
            conflicts.append(ConflictCode.SAFETY_MISSING)
        if decision.clarification_needed != bool(decision.missing_fields):
            conflicts.append(ConflictCode.CLARIFICATION_MISMATCH)
        if (
            facts.existing_appointment_mentioned is False
            and decision.utterance_intent is AgentIntent.RESCHEDULE_APPOINTMENT
            and "没有现有预约" in facts.source_text
        ):
            conflicts.append(ConflictCode.RESCHEDULE_WITHOUT_APPOINTMENT)
        if facts.small_talk_only and decision.utterance_intent is AgentIntent.NEW_REPAIR:
            conflicts.append(ConflictCode.SMALL_TALK_AS_REPAIR)
        return tuple(conflicts)

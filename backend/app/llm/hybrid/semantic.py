"""Typed semantic features between normalized evidence and business decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.agent.models import InterpretMessageInput
from app.llm.hybrid.models import NormalizedResidentFactsV1

_HUMAN_ACTOR = re.compile(r"(人工|真人|物业|值班人员|工作人员)")
_CALLBACK_ACTION = re.compile(r"(回电|联系|回话|沟通)")
_APPOINTMENT_REFERENCE = re.compile(r"(预约|已约|上门|到访|师傅|维修人员)")
_CONFIRMED_REFERENCE = re.compile(r"(已确认|已确定|已经约好|原来约|之前约|原预约)")
_RESCHEDULE_ACTION = re.compile(
    r"(改期|改到|换到|挪到|延后|向后延|顺延|往后推|重新安排|重新约|调整)"
)
_SELECTION_ACTION = re.compile(r"(我选|选择|选|就定|请定|确认|最后.{0,6}可以)")
_SELECTION_TARGET = re.compile(
    r"(候选|列表|第[一二三四五六七八九十]|最后|中间|师傅|时间段|时段|那一档)"
)
_UNAVAILABLE_DATE = re.compile(
    r"(还没想好|尚未确定|没有确定|还没定|稍后.{0,4}确认|"
    r"几点.{0,4}(没确定|不知道|未定))"
)
_ACTIONABLE_TIME = re.compile(
    r"(\d{1,2}:\d{2}|\d{1,2}点|两点|上午|下午|晚上|早上|全天|"
    r"改到(明天|后天)|"
    r"(上门|到访).{0,6}(时间|日期|安排).{0,8}(周|星期|明天|后天)|"
    r"上门预约.{0,8}(周|星期)|"
    r"(维修人员|师傅).{0,6}(到访|上门).{0,8}(周|星期))"
    r"|((周|星期).{0,6}上门)"
)
_NEGATED_CANCELLATION = re.compile(r"(不要|不|并非).{0,3}(取消|撤销)")
_GENERIC_FACILITY = re.compile(r"(设施|装置|设备)")
_GENERIC_FAILURE = re.compile(r"(不能使用|无法使用|不能正常|无法正常|出了故障|坏了)")
_SYSTEM_DURATION = re.compile(r"(维修|作业|服务).{0,3}时长")
_SYSTEM_RULE = re.compile(r"(系统|规则).{0,5}(确定|决定|安排)|按.{0,5}(系统|规则)")


@dataclass(frozen=True, slots=True)
class ResidentSemanticFeatures:
    """Closed, auditable features consumed by deterministic decision tables."""

    explicit_human_request: bool
    callback_request: bool
    existing_ticket_reference: bool
    existing_appointment_reference: bool
    confirmed_appointment_reference: bool
    new_booking_request: bool
    reschedule_request: bool
    slot_selection_request: bool
    availability_provided: bool
    availability_missing: bool
    cancellation_request: bool
    negated_cancellation: bool
    generic_facility_failure: bool
    unsupported_service_request: bool
    current_safety_evidence: bool
    correction_present: bool
    system_owned_duration: bool


def derive_semantic_features(
    facts: NormalizedResidentFactsV1,
    *,
    node_input: InterpretMessageInput,
) -> ResidentSemanticFeatures:
    """Derive compositional semantics without case IDs or full-sentence rules."""

    text = facts.source_text
    callback = bool(_HUMAN_ACTOR.search(text) and _CALLBACK_ACTION.search(text))
    contextual_appointment = any(
        _APPOINTMENT_REFERENCE.search(message.content)
        or any(marker in message.content for marker in ("原定日期", "可用时段", "候选时间"))
        for message in node_input.recent_conversation_messages
    )
    appointment_reference = bool(
        facts.existing_appointment_mentioned
        or _APPOINTMENT_REFERENCE.search(text)
        or contextual_appointment
    )
    confirmed_reference = bool(appointment_reference and _CONFIRMED_REFERENCE.search(text))
    reschedule = bool(
        facts.reschedule_request_mentioned
        or "改期" in text
        or (appointment_reference and _RESCHEDULE_ACTION.search(text))
    )
    context_has_candidates = any(
        _SELECTION_TARGET.search(message.content)
        for message in node_input.recent_conversation_messages
    )
    slot_selection = bool(
        _SELECTION_ACTION.search(text)
        and (_SELECTION_TARGET.search(text) or context_has_candidates)
    )
    system_duration = bool(_SYSTEM_DURATION.search(text) and _SYSTEM_RULE.search(text))
    availability_provided = bool(
        facts.time_expression_present
        and _ACTIONABLE_TIME.search(text)
        and not _UNAVAILABLE_DATE.search(text)
    )
    availability_missing = bool(reschedule and not availability_provided)
    negated_cancellation = _NEGATED_CANCELLATION.search(text) is not None
    return ResidentSemanticFeatures(
        explicit_human_request=facts.explicit_human_request or callback,
        callback_request=callback,
        existing_ticket_reference=facts.existing_ticket_mentioned,
        existing_appointment_reference=appointment_reference,
        confirmed_appointment_reference=confirmed_reference,
        new_booking_request=facts.booking_request_mentioned,
        reschedule_request=reschedule,
        slot_selection_request=slot_selection,
        availability_provided=availability_provided,
        availability_missing=availability_missing,
        cancellation_request=facts.explicit_cancellation_request and not negated_cancellation,
        negated_cancellation=negated_cancellation,
        generic_facility_failure=bool(
            _GENERIC_FACILITY.search(text) and _GENERIC_FAILURE.search(text)
        ),
        unsupported_service_request=bool(facts.unsupported_request_evidence),
        current_safety_evidence=bool(facts.safety_evidence),
        correction_present=facts.correction_present,
        system_owned_duration=system_duration,
    )

"""Deterministic normalization and evidence validation for extracted facts."""

from __future__ import annotations

import re
import unicodedata

from app.domain.enums import IssueCategory
from app.llm.hybrid.models import (
    CorrectedFact,
    EvidenceSpan,
    ExtractedResidentFactsV1,
    IssueCategoryEvidence,
    NormalizedResidentFactsV1,
)

_SPACE = re.compile(r"\s+")
_PUNCTUATION = str.maketrans({"，": ",", "。": ".", "：": ":", "；": ";", "？": "?", "！": "!"})

_CATEGORY_TERMS: dict[IssueCategory, tuple[str, ...]] = {
    IssueCategory.WATER_LEAK: (
        "漏水",
        "渗水",
        "滴水",
        "水管爆裂",
        "软管漏",
        "地面总有水",
    ),
    IssueCategory.ELECTRICAL: (
        "插座",
        "电线",
        "配电",
        "跳闸",
        "顶灯",
        "灯坏",
        "起火",
        "打火花",
    ),
    IssueCategory.DOOR_LOCK: ("门锁", "锁转不动", "反锁", "门把手"),
}

_LOCATION_TERMS = (
    "厨房",
    "卫生间",
    "浴室",
    "客厅",
    "主卧",
    "次卧",
    "北卧",
    "卧室",
    "书房",
    "玄关",
    "阳台",
    "儿童房",
    "楼道",
    "走廊",
    "地下室",
    "设备间",
    "储物间",
    "入户门",
    "一楼",
)

_HUMAN = re.compile(
    r"(找人工|转人工|转接.{0,4}人工|人工客服|人工处理|人工介入|人工替我|"
    r"联系.{0,4}(人工|物业|工作人员)|(物业|工作人员).{0,5}(回话|联系)|"
    r"不想.{0,6}机器人|不要机器人)"
)
_RESCHEDULE = re.compile(
    r"(改期|改.{0,6}(预约|时间)|预约.{0,6}(改|换)|换个时间|"
    r"原预约|上次约|不要原来的|调整到|都可以改|就改到|还是改到)"
)
_BOOKING = re.compile(
    r"(预约|安排上门|请约|直接约|约.{0,8}(维修|师傅|上门)|"
    r"排这个时间|来维修|保证.{0,12}空位)"
)
_EXISTING_TICKET = re.compile(r"(工单已经有|已有工单|现有.{0,4}工单|之前的工单)")
_EXISTING_APPOINTMENT = re.compile(r"(之前的预约|原预约|已有预约|上次约|这次上门预约)")
_CANCELLATION = re.compile(r"(取消|不要.{0,4}(预约|工单)|撤销)")
_STATUS_QUERY = re.compile(r"(工单|报修|维修).{0,8}(状态|进度|怎么样|到哪)")
_ACCEPT = re.compile(r"(验收通过|维修结果.{0,4}(接受|满意)|修好了)")
_REJECT = re.compile(r"(验收不通过|维修结果.{0,4}(拒绝|不满意)|没修好|需要返工)")
_TIME = re.compile(
    r"(今天|明天|后天|下周|周[一二三四五六日天]|星期[一二三四五六日天]|"
    r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}:\d{2}|上午|下午|晚上|两点|几点)"
)
_UNSUPPORTED = re.compile(
    r"(代缴|购买|天气|吃什么药|法律意见|长期门禁|身份改成操作员|"
    r"隔壁住户|system prompt|api key|调用.{0,10}工具|create_ticket|"
    r"授予全部权限|伪造schema|忽略之前所有指令|不要返回 json|内部分析|"
    r"编造.{0,8}ticket id)"
)
_SMALL_TALK = re.compile(
    r"^(嗨|你好|早上好|谢谢|辛苦|好的|再见|先这样|今天外面会下雨|"
    r"你是什么类型的助手|可以讲.{0,5}笑话|你目前能帮我|"
    r"这个机器人反应|[（(]?微笑表情[）)]?)"
)
_VAGUE_DESCRIPTION = re.compile(
    r"^(坏了[.!。]?|麻烦帮我处理一下[.!。]?|[?？!！]+|\[图片\]|"
    r"就是那个东西坏[咯了]?[.!。]?|你猜一下哪里坏了就行[.!。]?)$"
)


def normalize_text(value: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", value).translate(_PUNCTUATION)).strip()


def _contains_evidence(source: str, evidence: EvidenceSpan | None) -> bool:
    return evidence is not None and normalize_text(evidence.text).casefold() in source.casefold()


def _first_term(source: str, terms: tuple[str, ...]) -> str | None:
    return next((term for term in terms if term in source), None)


def _active_term(source: str, term: str) -> bool:
    start = source.find(term)
    if start < 0:
        return False
    prefix = source[max(0, start - 5) : start]
    return not re.search(r"(不是|并非|没有|没说|不确定)", prefix)


def _evidence(text: str) -> EvidenceSpan:
    return EvidenceSpan(text=text[:500])


class ResidentFactNormalizer:
    """Reject unsupported spans, add deterministic lexical facts, and sort outputs."""

    def normalize(
        self,
        extracted: ExtractedResidentFactsV1,
        *,
        current_user_message: str,
    ) -> NormalizedResidentFactsV1:
        source = normalize_text(current_user_message)
        rejected = 0
        updates: dict[str, object] = {}

        evidence_pairs = (
            ("issue_description", extracted.issue_description_evidence),
            ("location", extracted.location_evidence),
            ("existing_ticket", extracted.existing_ticket_evidence),
            ("existing_appointment", extracted.existing_appointment_evidence),
            ("booking_request", extracted.booking_request_evidence),
            ("reschedule_request", extracted.reschedule_request_evidence),
            ("human_request", extracted.human_request_evidence),
            ("cancellation_request", extracted.cancellation_request_evidence),
            ("status_query", extracted.status_query_evidence),
            ("acceptance_decision", extracted.acceptance_decision_evidence),
            ("time_expression", extracted.time_expression_evidence),
        )
        for prefix, evidence in evidence_pairs:
            if evidence is not None and not _contains_evidence(source, evidence):
                rejected += 1
                present_name = {
                    "issue_description": "issue_description_present",
                    "location": "location_mentioned",
                    "existing_ticket": "existing_ticket_mentioned",
                    "existing_appointment": "existing_appointment_mentioned",
                    "booking_request": "booking_request_mentioned",
                    "reschedule_request": "reschedule_request_mentioned",
                    "human_request": "explicit_human_request",
                    "cancellation_request": "explicit_cancellation_request",
                    "status_query": "status_query_mentioned",
                    "acceptance_decision": "acceptance_decision",
                    "time_expression": "time_expression_present",
                }[prefix]
                updates[present_name] = None if present_name == "acceptance_decision" else False
                updates[f"{prefix}_evidence"] = None
                if prefix in {"issue_description", "location", "time_expression"}:
                    updates[f"{prefix}_text"] = None

        category_evidence = tuple(
            item
            for item in extracted.issue_category_evidence
            if _contains_evidence(source, item.evidence)
        )
        rejected += len(extracted.issue_category_evidence) - len(category_evidence)
        existing_categories = {item.category for item in category_evidence}
        for category, terms in _CATEGORY_TERMS.items():
            term = next((item for item in terms if _active_term(source, item)), None)
            if term is not None and category not in existing_categories:
                category_evidence += (
                    IssueCategoryEvidence(category=category, evidence=EvidenceSpan(text=term)),
                )
        updates["issue_category_evidence"] = tuple(
            sorted(category_evidence, key=lambda item: item.category.value)
        )

        location = extracted.location_text
        location_evidence = extracted.location_evidence
        uncertainty = any(
            marker in source
            for marker in ("哪个门", "没想起来", "不确定是不是", "哪里坏", "什么地方")
        )
        if (not location or not _contains_evidence(source, location_evidence)) and not uncertainty:
            term = _first_term(source, _LOCATION_TERMS)
            if term is None:
                custom_location = re.search(r"[A-Za-z]座[^,，。.!！?？]{0,12}房间", source)
                term = custom_location.group(0) if custom_location is not None else None
            if term is not None:
                location = term
                location_evidence = EvidenceSpan(text=term)
        if uncertainty:
            location = None
            location_evidence = None
        updates.update(
            location_mentioned=location is not None,
            location_text=location,
            location_evidence=location_evidence,
        )

        description_present = extracted.issue_description_present
        description_text = extracted.issue_description_text
        description_evidence = extracted.issue_description_evidence
        has_issue_language = bool(category_evidence) or any(
            marker in source
            for marker in (
                "故障",
                "堵住",
                "打不开",
                "合不上",
                "不热",
                "噪声",
                "开不了",
                "掉下",
                "鼓起",
                "松动",
            )
        )
        if (
            not description_present
            and has_issue_language
            and not _VAGUE_DESCRIPTION.fullmatch(source.casefold())
            and "具体表现还没确认" not in source
            and "不知道坏的是什么" not in source
        ):
            description_present = True
            description_text = source
            description_evidence = _evidence(source)
        updates.update(
            issue_description_present=description_present,
            issue_description_text=description_text,
            issue_description_evidence=description_evidence,
        )

        def enrich(
            present_field: str,
            evidence_field: str,
            pattern: re.Pattern[str],
            *,
            allowed: bool = True,
        ) -> None:
            if bool(updates.get(present_field, getattr(extracted, present_field))) or not allowed:
                return
            match = pattern.search(source.casefold())
            if match is not None:
                updates[present_field] = True
                updates[evidence_field] = _evidence(match.group(0))

        enrich("explicit_human_request", "human_request_evidence", _HUMAN)
        enrich("reschedule_request_mentioned", "reschedule_request_evidence", _RESCHEDULE)
        enrich("booking_request_mentioned", "booking_request_evidence", _BOOKING)
        enrich("existing_ticket_mentioned", "existing_ticket_evidence", _EXISTING_TICKET)
        enrich(
            "existing_appointment_mentioned",
            "existing_appointment_evidence",
            _EXISTING_APPOINTMENT,
            allowed="没有现有预约" not in source,
        )
        enrich("time_expression_present", "time_expression_evidence", _TIME)
        enrich(
            "explicit_cancellation_request",
            "cancellation_request_evidence",
            _CANCELLATION,
        )
        enrich("status_query_mentioned", "status_query_evidence", _STATUS_QUERY)
        if extracted.acceptance_decision is None:
            from app.agent.enums import AcceptanceDecision

            accept = _ACCEPT.search(source)
            reject = _REJECT.search(source)
            if accept is not None:
                updates["acceptance_decision"] = AcceptanceDecision.ACCEPT
                updates["acceptance_decision_evidence"] = _evidence(accept.group(0))
            elif reject is not None:
                updates["acceptance_decision"] = AcceptanceDecision.REJECT
                updates["acceptance_decision_evidence"] = _evidence(reject.group(0))
        if bool(updates.get("time_expression_present", extracted.time_expression_present)):
            time_evidence = updates.get(
                "time_expression_evidence", extracted.time_expression_evidence
            )
            if isinstance(time_evidence, EvidenceSpan):
                updates["time_expression_text"] = time_evidence.text
        if not extracted.unsupported_request_evidence:
            match = _UNSUPPORTED.search(source.casefold())
            if match is not None:
                updates["unsupported_request_evidence"] = (_evidence(match.group(0)),)
        if not extracted.small_talk_only:
            updates["small_talk_only"] = (
                _SMALL_TALK.search(source.casefold()) is not None
                and not has_issue_language
                and not bool(
                    updates.get("explicit_human_request", extracted.explicit_human_request)
                )
            )

        safety_evidence = tuple(
            item for item in extracted.safety_evidence if _contains_evidence(source, item.evidence)
        )
        rejected += len(extracted.safety_evidence) - len(safety_evidence)
        updates["safety_evidence"] = tuple(
            sorted(
                {
                    (item.signal, normalize_text(item.evidence.text)): item
                    for item in safety_evidence
                }.values(),
                key=lambda item: (item.signal.value, item.evidence.text),
            )
        )

        correction = extracted.correction_present or any(
            marker in source
            for marker in (
                "纠正",
                "说错",
                "不是",
                "改一下",
                "改到",
                "其实",
                "不准确",
                "先不",
            )
        )
        corrected_fields = set(extracted.corrected_fields)
        if correction and not corrected_fields:
            if any(marker in source for marker in ("时间", "日期", "上午", "下午", "周")):
                corrected_fields.add(CorrectedFact.AVAILABILITY)
            elif any(term in source for term in _LOCATION_TERMS):
                corrected_fields.add(CorrectedFact.ISSUE_LOCATION)
            else:
                corrected_fields.add(CorrectedFact.OTHER)
        updates.update(
            correction_present=correction,
            corrected_fields=tuple(sorted(corrected_fields, key=lambda item: item.value)),
            rejected_evidence_count=rejected,
            conflict_codes=(),
        )
        payload = extracted.model_dump()
        payload.update(updates)
        payload["source_text"] = source
        return NormalizedResidentFactsV1.model_validate(payload)

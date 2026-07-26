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
        "淌水",
        "往下流水",
        "管道接口湿",
        "水龙头",
        "水管爆了",
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
        "灯不亮",
        "灯一直闪",
        "灯闪",
        "开关",
        "电表箱",
        "裸线",
        "没电",
        "起火",
        "打火花",
    ),
    IssueCategory.DOOR_LOCK: (
        "门锁",
        "锁芯",
        "钥匙",
        "锁转不动",
        "反锁",
        "被锁",
        "门把手",
        "门打不开",
    ),
}

_LOCATION_TERMS = (
    "厨房",
    "卫生间",
    "主卫",
    "浴室",
    "客厅",
    "餐厅",
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
    "衣帽间",
    "入户门",
    "一楼",
)

_HUMAN = re.compile(
    r"(找人工|转人工|转接.{0,4}人工|人工客服|人工处理|人工介入|人工替我|"
    r"联系.{0,4}(人工|物业|工作人员)|(物业|工作人员).{0,5}(回话|联系)|"
    r"不想.{0,6}机器人|不要机器人|真人物业|人工跟进|直接交给.{0,6}(物业|值班人员)|"
    r"安排人工联系|和真人.{0,6}沟通|马上转人工)"
)
_RESCHEDULE = re.compile(
    r"(改期|改.{0,6}(预约|时间)|预约.{0,6}(改|换)|换个时间|"
    r"原预约|原来的预约|原来约|上次约|不要原来的|调整到|都可以改|就改到|还是改到|改成|"
    r"已约.{0,8}改|预约.{0,8}挪到|预约.{0,8}换到|预约.{0,8}往后推|"
    r"上门时间.{0,8}改|重新约)"
)
_BOOKING = re.compile(
    r"(预约|安排上门|请约|直接约|约.{0,8}(维修|师傅|上门)|"
    r"排这个时间|来维修|保证.{0,12}空位)"
)
_EXISTING_TICKET = re.compile(r"(工单已经有|已有工单|现有.{0,4}工单|之前的工单)")
_EXISTING_APPOINTMENT = re.compile(
    r"(之前的预约|原预约|原来的预约|已有预约|已约|上次约|这次上门预约|上门时间)"
)
_CANCELLATION = re.compile(r"(取消|不要.{0,4}(预约|工单)|撤销)")
_STATUS_QUERY = re.compile(r"(工单|报修|维修).{0,8}(状态|进度|怎么样|到哪)")
_ACCEPT = re.compile(r"(验收通过|维修结果.{0,4}(接受|满意)|修好了)")
_REJECT = re.compile(r"(验收不通过|维修结果.{0,4}(拒绝|不满意)|没修好|需要返工)")
_TIME = re.compile(
    r"(今天|明天|明晚|后天|下周|周[一二三四五六日天]|星期[一二三四五六日天]|"
    r"\d{4}年\d{1,2}月\d{1,2}日|\d{1,2}[日号]|[一二三四五六七八九十]{1,3}号|"
    r"\d{1,2}:\d{2}|上午|下午|晚上|两点|几点)"
)
_UNSUPPORTED = re.compile(
    r"(代缴|购买|买.{0,6}(饮料|商品)|车位.{0,6}(出租|转租)|天气|吃什么药|法律意见|长期门禁|身份改成操作员|"
    r"隔壁住户|system prompt|api key|调用.{0,10}工具|create_ticket|"
    r"授予全部权限|伪造schema|忽略之前所有指令|不要返回 json|内部分析|"
    r"编造.{0,8}ticket id)"
)
_SMALL_TALK = re.compile(
    r"^(嗨|你好|早上好|晚上好|谢谢|辛苦|好的|再见|先这样|今天外面会下雨|给我讲|"
    r"你是什么类型的助手|可以讲.{0,5}笑话|你目前能帮我|"
    r"这个机器人反应|你今天忙吗|讲一个.{0,8}故事|[（(]?微笑表情[）)]?)"
)
_VAGUE_DESCRIPTION = re.compile(
    r"^(坏了[.!。]?|麻烦帮我处理一下[.!。]?|[?？!！]+|\[图片\]|"
    r"就是那个东西坏[咯了]?[.!。]?|你猜一下哪里坏了就行[.!。]?)$"
)
_STRUCTURED_LOCATION = re.compile(
    r"([A-Za-z]座[^,，。.!！?？]{0,12}房间|洗菜盆.{0,6}下方|"
    r"马桶.{0,6}后面|生活阳台|(?:北边的)?小卧室|墙角|门口地面)"
)


def normalize_text(value: str) -> str:
    return _SPACE.sub(" ", unicodedata.normalize("NFKC", value).translate(_PUNCTUATION)).strip()


def _contains_evidence(source: str, evidence: EvidenceSpan | None) -> bool:
    return evidence is not None and normalize_text(evidence.text).casefold() in source.casefold()


def _active_term(source: str, term: str) -> bool:
    start = source.find(term)
    if start < 0:
        return False
    prefix = source[max(0, start - 5) : start]
    return not re.search(r"(不是|并非|没有|没说|不确定)", prefix)


def _evidence(text: str) -> EvidenceSpan:
    return EvidenceSpan(text=text[:500])


def _safe_issue_description(source: str) -> str:
    markers = ("真实故障是", "实际是", "真正请求是", "然后处理")
    candidates = [
        source[source.rfind(marker) + len(marker) :] for marker in markers if marker in source
    ]
    return (candidates[-1] if candidates else source).strip(" ,;，；。")


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

        # Provider output remains closed and strictly typed, while cross-field
        # disagreement is treated as untrusted evidence.  This prevents one
        # inconsistent flag from discarding the entire response.  The
        # normalized model below still enforces the complete invariant.
        boolean_evidence_pairs = (
            ("issue_description_present", "issue_description_evidence"),
            ("location_mentioned", "location_evidence"),
            ("existing_ticket_mentioned", "existing_ticket_evidence"),
            ("existing_appointment_mentioned", "existing_appointment_evidence"),
            ("booking_request_mentioned", "booking_request_evidence"),
            ("reschedule_request_mentioned", "reschedule_request_evidence"),
            ("explicit_human_request", "human_request_evidence"),
            ("explicit_cancellation_request", "cancellation_request_evidence"),
            ("status_query_mentioned", "status_query_evidence"),
            ("time_expression_present", "time_expression_evidence"),
        )
        for present_field, evidence_field in boolean_evidence_pairs:
            present = bool(getattr(extracted, present_field))
            evidence = getattr(extracted, evidence_field)
            if present != (evidence is not None):
                rejected += 1
                updates[present_field] = False
                updates[evidence_field] = None
        if (extracted.acceptance_decision is not None) != (
            extracted.acceptance_decision_evidence is not None
        ):
            rejected += 1
            updates["acceptance_decision"] = None
            updates["acceptance_decision_evidence"] = None
        for present_field, text_field in (
            ("issue_description_present", "issue_description_text"),
            ("location_mentioned", "location_text"),
            ("time_expression_present", "time_expression_text"),
        ):
            present = bool(updates.get(present_field, getattr(extracted, present_field)))
            text = getattr(extracted, text_field)
            if present != (text is not None):
                rejected += 1
                updates[present_field] = False
                updates[text_field] = None
                updates[text_field.replace("_text", "_evidence")] = None

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

        semantic_flags = (
            (
                "explicit_human_request",
                "human_request_evidence",
                extracted.human_request_evidence,
                _HUMAN,
            ),
            (
                "booking_request_mentioned",
                "booking_request_evidence",
                extracted.booking_request_evidence,
                _BOOKING,
            ),
            (
                "reschedule_request_mentioned",
                "reschedule_request_evidence",
                extracted.reschedule_request_evidence,
                _RESCHEDULE,
            ),
            (
                "explicit_cancellation_request",
                "cancellation_request_evidence",
                extracted.cancellation_request_evidence,
                _CANCELLATION,
            ),
            (
                "status_query_mentioned",
                "status_query_evidence",
                extracted.status_query_evidence,
                _STATUS_QUERY,
            ),
        )
        for present_field, evidence_field, evidence, pattern in semantic_flags:
            present = bool(updates.get(present_field, getattr(extracted, present_field)))
            if present and (
                evidence is None or pattern.search(normalize_text(evidence.text)) is None
            ):
                rejected += 1
                updates[present_field] = False
                updates[evidence_field] = None
        if extracted.acceptance_decision is not None:
            evidence = extracted.acceptance_decision_evidence
            accepted = evidence is not None and (
                _ACCEPT.search(normalize_text(evidence.text)) is not None
                or _REJECT.search(normalize_text(evidence.text)) is not None
            )
            if not accepted:
                rejected += 1
                updates["acceptance_decision"] = None
                updates["acceptance_decision_evidence"] = None

        category_evidence = tuple(
            item
            for item in extracted.issue_category_evidence
            if _contains_evidence(source, item.evidence)
            and any(
                _active_term(source, term)
                for term in _CATEGORY_TERMS[item.category]
                if term in normalize_text(item.evidence.text)
            )
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

        location = updates.get("location_text", extracted.location_text)
        location_evidence = updates.get("location_evidence", extracted.location_evidence)
        if not isinstance(location, str):
            location = None
        if not isinstance(location_evidence, EvidenceSpan):
            location_evidence = None
        if location is not None and not (
            any(term in normalize_text(location) for term in _LOCATION_TERMS)
            or _STRUCTURED_LOCATION.search(normalize_text(location)) is not None
        ):
            rejected += 1
            location = None
            location_evidence = None
        uncertainty = any(
            marker in source
            for marker in ("哪个门", "没想起来", "不确定是不是", "哪里坏", "什么地方")
        )
        if (not location or not _contains_evidence(source, location_evidence)) and not uncertainty:
            custom_location = _STRUCTURED_LOCATION.search(source)
            term = custom_location.group(0) if custom_location is not None else None
            if term is None:
                active_locations = tuple(
                    item for item in _LOCATION_TERMS if _active_term(source, item)
                )
                term = max(active_locations, key=source.rfind) if active_locations else None
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

        description_present = bool(
            updates.get("issue_description_present", extracted.issue_description_present)
        )
        description_text = updates.get("issue_description_text", extracted.issue_description_text)
        description_evidence = updates.get(
            "issue_description_evidence", extracted.issue_description_evidence
        )
        if not isinstance(description_text, str):
            description_text = None
        if not isinstance(description_evidence, EvidenceSpan):
            description_evidence = None
        if _VAGUE_DESCRIPTION.fullmatch(source.casefold()):
            if description_present:
                rejected += 1
            description_present = False
            description_text = None
            description_evidence = None
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
            description_text = _safe_issue_description(source)
            description_evidence = _evidence(description_text)
        elif description_present and description_text is not None:
            description_text = _safe_issue_description(normalize_text(description_text))
            description_evidence = _evidence(description_text)
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
        valid_windows = tuple(
            window
            for window in extracted.availability_windows
            if window.starts_at.tzinfo is not None
            and window.ends_at.tzinfo is not None
            and window.ends_at > window.starts_at
        )
        rejected += len(extracted.availability_windows) - len(valid_windows)
        updates["availability_windows"] = valid_windows
        supported_unsupported = tuple(
            item
            for item in extracted.unsupported_request_evidence
            if _UNSUPPORTED.search(normalize_text(item.text).casefold()) is not None
        )
        rejected += len(extracted.unsupported_request_evidence) - len(supported_unsupported)
        updates["unsupported_request_evidence"] = supported_unsupported
        if not supported_unsupported:
            match = _UNSUPPORTED.search(source.casefold())
            if match is not None:
                updates["unsupported_request_evidence"] = (_evidence(match.group(0)),)
        deterministic_small_talk = (
            _SMALL_TALK.search(source.casefold()) is not None
            and not has_issue_language
            and not bool(updates.get("explicit_human_request", extracted.explicit_human_request))
        )
        if extracted.small_talk_only and not deterministic_small_talk:
            rejected += 1
        updates["small_talk_only"] = deterministic_small_talk

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

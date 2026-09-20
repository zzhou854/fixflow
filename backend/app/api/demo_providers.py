"""Offline-only providers used by the explicitly labelled demonstration runtime."""

from __future__ import annotations

import hashlib
import json
import math
import re
from calendar import monthrange
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.agent.enums import AgentIntent, SafetyFlag
from app.agent.models import (
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from app.domain.enums import IssueCategory
from app.policy.constants import POLICY_EMBEDDING_DIMENSION
from app.policy.models import EmbeddingProfile, EmbeddingResult
from app.policy.normalization import normalize_policy_text


class DemoScriptedLLMProvider:
    """Deterministic offline language script; it is not an online model."""

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        context = self._context(messages[-1].content)
        message = str(context.get("current_message", context.get("current_user_message", "")))
        known = dict(context.get("known_issue_fields", {}))
        # The sanitized prompt exposes durable intent as a flat allowlisted
        # field. Keep compatibility with older nested demo payloads as well.
        summary = dict(context.get("current_state_summary", {}))
        if "task_intent" not in summary:
            summary["task_intent"] = context.get("current_task_intent")
        lowered = message.casefold()
        message_category = self._category(message)
        category = known.get("issue_category") or message_category
        intent = self._intent(message, summary)
        payload: dict[str, object] = {"utterance_intent": intent}
        if category:
            payload["issue_category"] = category
        location = self._location(message)
        if location:
            payload["issue_location"] = location
        flags = self._safety(lowered)
        if flags:
            payload["safety_flags"] = flags
        window = self._availability(message, context)
        if window:
            payload["user_availability_windows"] = [window]
        if intent == AgentIntent.NEW_REPAIR.value or (
            intent == AgentIntent.PROVIDE_INFORMATION.value
            and (window is None or message_category is not None or location is not None)
        ):
            payload["issue_description_update"] = message
        if intent == AgentIntent.REQUEST_HUMAN.value:
            payload["requested_human"] = True
        return StructuredLLMResult(
            payload=payload,
            provider="fixflow-demo-scripted",
            model=model_config.model,
            prompt_name=model_config.prompt_name,
            prompt_version=model_config.prompt_version,
        )

    async def generate_response(
        self,
        *,
        messages: Sequence[LLMMessage],
        model_config: LLMRequestConfig,
    ) -> TextLLMResult:
        return TextLLMResult(
            text="已按当前已核验的工单与预约信息处理。请查看页面中的最新状态。",
            provider="fixflow-demo-scripted",
            model=model_config.model,
            prompt_name=model_config.prompt_name,
            prompt_version=model_config.prompt_version,
        )

    async def health_check(self) -> bool:
        return True

    @staticmethod
    def _context(content: str) -> dict[str, Any]:
        marker = content.find("{")
        return json.loads(content[marker:]) if marker >= 0 else {}

    @staticmethod
    def _category(message: str) -> str | None:
        if any(word in message for word in ("漏水", "渗水", "水管", "水龙头")):
            return IssueCategory.WATER_LEAK.value
        if any(word in message for word in ("电", "插座", "跳闸", "灯")):
            return IssueCategory.ELECTRICAL.value
        if any(word in message for word in ("门锁", "锁", "钥匙")):
            return IssueCategory.DOOR_LOCK.value
        return None

    @staticmethod
    def _intent(message: str, summary: dict[str, Any]) -> str:
        if "改期" in message or "换个时间" in message:
            return AgentIntent.RESCHEDULE_APPOINTMENT.value
        if "进度" in message or "状态" in message:
            return AgentIntent.QUERY_TICKET_STATUS.value
        if "人工" in message or "物业" in message:
            return AgentIntent.REQUEST_HUMAN.value
        if "取消预约" in message:
            return AgentIntent.CANCEL_APPOINTMENT.value
        if "取消工单" in message:
            return AgentIntent.CANCEL_TICKET.value
        if summary.get("task_intent") not in (None, "UNKNOWN"):
            return AgentIntent.PROVIDE_INFORMATION.value
        return AgentIntent.NEW_REPAIR.value

    @staticmethod
    def _location(message: str) -> str | None:
        if re.search(r"(?:主|次|客|小)?卧(?:室)?", message):
            return "卧室"
        for value in (
            "儿童房",
            "厨房",
            "卫生间",
            "客厅",
            "书房",
            "入户门",
            "阳台",
        ):
            if value in message:
                return value
        match = re.search(r"([一二三四五六七八九十\d]+(?:楼|层|号房|室))", message)
        return match.group(1) if match else None

    @staticmethod
    def _safety(message: str) -> list[str]:
        flags: list[str] = []
        if "冒烟" in message or "触电" in message or "火花" in message:
            flags.append(SafetyFlag.ELECTRICAL_HAZARD.value)
        if "大量漏水" in message or "正在淹" in message:
            flags.append(SafetyFlag.ACTIVE_FLOODING.value)
        return flags

    @staticmethod
    def _availability(message: str, context: dict[str, Any]) -> dict[str, str] | None:
        date_match = re.search(
            r"(?:\d{1,2}|[一二两三四五六七八九十]{1,3})[号日]", message
        )
        point_time_match = re.search(
            r"(?:\d{1,2}|[零〇一二两三四五六七八九十]{1,3})点", message
        )
        colon_time_match = re.search(r"\d{1,2}\s*[:：]\s*\d{2}", message)
        time_markers = ("今天", "明天", "后天", "上午", "下午", "中午", "晚上")
        if (
            not any(word in message for word in time_markers)
            and date_match is None
            and point_time_match is None
            and colon_time_match is None
        ):
            return None
        reference = str(context.get("reference_time"))
        timezone_name = str(context.get("timezone_name", "Asia/Shanghai"))

        current = datetime.fromisoformat(reference).astimezone(ZoneInfo(timezone_name))
        target_date = DemoScriptedLLMProvider._explicit_date(message, current)
        if date_match is not None and target_date is None:
            return None
        if target_date is None:
            # A clock without a day means today in ordinary conversation.  Do
            # not silently roll an already-passed clock into tomorrow; the
            # workflow validates it and asks the resident for a future time.
            days = 2 if "后天" in message else 1 if "明天" in message else 0
            target_date = current + timedelta(days=days)
        explicit_range = DemoScriptedLLMProvider._explicit_time_range(message)
        explicit_time = DemoScriptedLLMProvider._explicit_time(message)
        if (point_time_match is not None or colon_time_match is not None) and explicit_time is None:
            return None
        if explicit_range is not None:
            (hour, minute), (end_hour, end_minute) = explicit_range
            duration = timedelta(hours=end_hour, minutes=end_minute) - timedelta(
                hours=hour, minutes=minute
            )
            if duration <= timedelta(0):
                return None
        elif explicit_time is not None:
            hour, minute = explicit_time
            duration = timedelta(hours=2)
        else:
            hour = 9 if "上午" in message else 14
            minute = 0
            duration = timedelta(hours=4)
        start = target_date.replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return {"starts_at": start.isoformat(), "ends_at": (start + duration).isoformat()}

    @staticmethod
    def _explicit_time_range(
        message: str,
    ) -> tuple[tuple[int, int], tuple[int, int]] | None:
        match = re.search(
            r"(?P<start_period>上午|中午|下午|晚上)?\s*"
            r"(?P<start_hour>\d{1,2}|[零〇一二两三四五六七八九十]{1,3})点"
            r"(?P<start_half>半)?\s*(?:到|至|[-—~～])\s*"
            r"(?P<end_period>上午|中午|下午|晚上)?\s*"
            r"(?P<end_hour>\d{1,2}|[零〇一二两三四五六七八九十]{1,3})点"
            r"(?P<end_half>半)?",
            message,
        )
        if match is None:
            return None

        def clock(hour_text: str, half: str | None, period: str | None) -> tuple[int, int]:
            hour = (
                int(hour_text)
                if hour_text.isdigit()
                else DemoScriptedLLMProvider._chinese_number(hour_text)
            )
            if period in {"下午", "晚上"} and hour < 12:
                hour += 12
            elif period == "中午" and hour < 11:
                hour += 12
            return hour, 30 if half else 0

        start_period = match.group("start_period")
        start = clock(match.group("start_hour"), match.group("start_half"), start_period)
        end = clock(
            match.group("end_hour"),
            match.group("end_half"),
            match.group("end_period") or start_period,
        )
        if not all(0 <= hour <= 23 for hour in (start[0], end[0])):
            return None
        return start, end

    @staticmethod
    def _explicit_date(message: str, current: datetime) -> datetime | None:
        match = re.search(
            r"(?:(?P<year>\d{4})年)?"
            r"(?:(?P<month>\d{1,2}|[一二两三四五六七八九十]{1,3})月)?"
            r"(?P<day>\d{1,2}|[一二两三四五六七八九十]{1,3})[号日]",
            message,
        )
        if match is None:
            return None

        def number(value: str) -> int:
            return int(value) if value.isdigit() else DemoScriptedLLMProvider._chinese_number(value)

        explicit_year = match.group("year")
        explicit_month = match.group("month")
        year = int(explicit_year) if explicit_year else current.year
        month = number(explicit_month) if explicit_month else current.month
        day = number(match.group("day"))

        def valid_date(candidate_year: int, candidate_month: int) -> datetime | None:
            if not 1 <= candidate_month <= 12:
                return None
            if not 1 <= day <= monthrange(candidate_year, candidate_month)[1]:
                return None
            return current.replace(year=candidate_year, month=candidate_month, day=day)

        candidate = valid_date(year, month)
        if candidate is None:
            return None
        if candidate.date() >= current.date() or explicit_year:
            return candidate
        if explicit_month:
            return valid_date(year + 1, month)
        next_month = 1 if month == 12 else month + 1
        next_year = year + 1 if month == 12 else year
        return valid_date(next_year, next_month)

    @staticmethod
    def _explicit_time(message: str) -> tuple[int, int] | None:
        colon_match = re.search(
            r"(?P<period>上午|中午|下午|晚上)?\s*"
            r"(?P<hour>\d{1,2})\s*[:：]\s*(?P<minute>\d{2})",
            message,
        )
        if colon_match is not None:
            hour = int(colon_match.group("hour"))
            minute = int(colon_match.group("minute"))
            period = colon_match.group("period")
            if period in {"下午", "晚上"} and hour < 12:
                hour += 12
            elif period == "中午" and hour < 11:
                hour += 12
            return (hour, minute) if 0 <= hour <= 23 and 0 <= minute <= 59 else None

        match = re.search(
            r"(?P<period>上午|中午|下午|晚上)?\s*"
            r"(?P<hour>\d{1,2}|[零〇一二两三四五六七八九十]{1,3})点"
            r"(?P<half>半)?",
            message,
        )
        if match is None:
            return None
        raw_hour = match.group("hour")
        hour = (
            int(raw_hour)
            if raw_hour.isdigit()
            else DemoScriptedLLMProvider._chinese_number(raw_hour)
        )
        period = match.group("period")
        if period in {"下午", "晚上"} and hour < 12:
            hour += 12
        elif period == "中午" and hour < 11:
            hour += 12
        if not 0 <= hour <= 23:
            return None
        return hour, 30 if match.group("half") else 0

    @staticmethod
    def _chinese_number(value: str) -> int:
        digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if value == "十":
            return 10
        if "十" in value:
            tens, ones = value.split("十", 1)
            return (digits.get(tens, 1) * 10) + (digits.get(ones, 0) if ones else 0)
        return digits[value]


class DemoDeterministicEmbeddingProvider:
    """Stable character-bigram hashing for offline policy demonstration only."""

    def __init__(self) -> None:
        self._profile = EmbeddingProfile(
            provider="fixflow-demo",
            model="character-bigram-hash",
            dimension=POLICY_EMBEDDING_DIMENSION,
            profile_version="v1",
        )

    @property
    def profile(self) -> EmbeddingProfile:
        return self._profile

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[EmbeddingResult]:
        return [EmbeddingResult(vector=self._vector(text), profile=self.profile) for text in texts]

    async def embed_query(self, text: str) -> EmbeddingResult:
        return EmbeddingResult(vector=self._vector(text), profile=self.profile)

    async def health_check(self) -> bool:
        return True

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        normalized = normalize_policy_text(text).replace(" ", "")
        tokens = tuple(
            normalized[index : index + 2] for index in range(max(1, len(normalized) - 1))
        )
        values = [0.0] * POLICY_EMBEDDING_DIMENSION
        for token in tokens:
            digest = hashlib.sha256(token.encode()).digest()
            values[int.from_bytes(digest[:4], "big") % POLICY_EMBEDDING_DIMENSION] += 1
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return tuple(value / norm for value in values)

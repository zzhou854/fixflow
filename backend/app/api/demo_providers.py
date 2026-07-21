"""Offline-only providers used by the explicitly labelled demonstration runtime."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Sequence
from datetime import timedelta
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
        message = str(context.get("current_user_message", ""))
        known = dict(context.get("known_issue_fields", {}))
        summary = dict(context.get("current_state_summary", {}))
        lowered = message.casefold()
        category = known.get("issue_category") or self._category(message)
        intent = self._intent(message, summary)
        payload: dict[str, object] = {"utterance_intent": intent}
        if category:
            payload["issue_category"] = category
        location = self._location(message)
        if location:
            payload["issue_location"] = location
        if intent in {AgentIntent.NEW_REPAIR.value, AgentIntent.PROVIDE_INFORMATION.value}:
            payload["issue_description_update"] = message
        flags = self._safety(lowered)
        if flags:
            payload["safety_flags"] = flags
        window = self._availability(message, context)
        if window:
            payload["user_availability_windows"] = [window]
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
        for value in ("厨房", "卫生间", "客厅", "卧室", "入户门", "阳台"):
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
        if not any(word in message for word in ("明天", "后天", "上午", "下午")):
            return None
        reference = str(context.get("reference_time"))
        timezone_name = str(context.get("timezone_name", "Asia/Shanghai"))
        from datetime import datetime

        current = datetime.fromisoformat(reference).astimezone(ZoneInfo(timezone_name))
        days = 2 if "后天" in message else 1
        hour = 9 if "上午" in message else 14
        start = (current + timedelta(days=days)).replace(
            hour=hour, minute=0, second=0, microsecond=0
        )
        return {"starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=4)).isoformat()}


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

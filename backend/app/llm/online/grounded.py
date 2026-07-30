"""Grounded response drafting that cannot alter business outcomes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.agent.enums import LLMRole
from app.agent.models import LLMMessage, LLMRequestConfig
from app.agent.ports import LLMProvider
from app.llm.errors import LLMProviderError


class ResponseTone(StrEnum):
    WARM = "WARM"
    CONCISE = "CONCISE"
    REASSURING = "REASSURING"


class GroundedFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    fact_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    safe_text: str = Field(min_length=1, max_length=300)


class GroundedResponseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    template_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    message_outcome: str = Field(min_length=1, max_length=100)
    required_user_action: str | None = Field(default=None, max_length=500)
    facts: tuple[GroundedFact, ...] = Field(default=(), max_length=30)


class GroundedResponseDraft(BaseModel):
    """The model selects presentation only; it cannot introduce free-form facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    template_id: str
    tone: ResponseTone
    included_fact_ids: tuple[str, ...] = Field(default=(), max_length=30)


class GroundedResponseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str
    template_id: str
    message_outcome: str
    required_user_action: str | None
    used_model: bool


CRITICAL_TEMPLATE_IDS = frozenset(
    {
        "AUTHORIZATION_DENIED",
        "HUMAN_REVIEW_REQUIRED",
        "SAFETY_REVIEW_REQUIRED",
        "TICKET_CREATED",
        "APPOINTMENT_BOOKED",
        "APPOINTMENT_RESCHEDULED",
        "UNKNOWN_COMMIT",
        "CANCELLED",
        "CLOSED",
    }
)

_TEMPLATES: Mapping[str, str] = {
    "AUTHORIZATION_DENIED": "抱歉，当前账号没有权限处理这个房屋。",
    "HUMAN_REVIEW_REQUIRED": "这次情况需要工作人员进一步处理，我们已经为你转交人工。",
    "SAFETY_REVIEW_REQUIRED": "为确保安全，请先停止相关操作，我们已经转交工作人员处理。",
    "TICKET_CREATED": "报修工单已经创建。",
    "APPOINTMENT_BOOKED": "上门时间已经预约成功。",
    "APPOINTMENT_RESCHEDULED": "新的上门时间已经确认。",
    "UNKNOWN_COMMIT": "提交结果暂时无法确认，系统正在核对，请不要重复操作。",
    "CANCELLED": "本次处理已取消。",
    "CLOSED": "本次报修已经完成并关闭。",
    "GENERIC_UPDATE": "你的请求已经处理。",
}

_PREFIXES: Mapping[ResponseTone, str] = {
    ResponseTone.WARM: "好的，",
    ResponseTone.CONCISE: "",
    ResponseTone.REASSURING: "请放心，",
}


class GroundedResponseProvider:
    """Renders server-owned templates; model failure never changes the outcome."""

    def __init__(self, provider: LLMProvider, *, model: str) -> None:
        self._provider = provider
        self._model = model

    async def compose(self, request: GroundedResponseRequest) -> GroundedResponseResult:
        if request.template_id in CRITICAL_TEMPLATE_IDS:
            return self._render(request, ResponseTone.CONCISE, (), used_model=False)
        prompt = (
            "Choose only tone and fact identifiers. Do not write prose or change outcome/action. "
            f"template_id={request.template_id}; outcome={request.message_outcome}; "
            f"allowed_fact_ids={[fact.fact_id for fact in request.facts]}"
        )
        try:
            raw = await self._provider.generate_structured(
                messages=(LLMMessage(role=LLMRole.SYSTEM, content=prompt),),
                response_model=GroundedResponseDraft,
                model_config=LLMRequestConfig(
                    model=self._model,
                    prompt_name="grounded_response",
                    prompt_version="1.0.0",
                    temperature=0,
                    max_output_tokens=200,
                ),
            )
            draft = GroundedResponseDraft.model_validate(raw.payload)
            self._validate_draft(request, draft)
        except (LLMProviderError, ValueError):
            return self._render(request, ResponseTone.CONCISE, (), used_model=False)
        return self._render(
            request,
            draft.tone,
            draft.included_fact_ids,
            used_model=True,
        )

    @staticmethod
    def _validate_draft(request: GroundedResponseRequest, draft: GroundedResponseDraft) -> None:
        if draft.template_id != request.template_id:
            raise ValueError("template identity changed")
        allowed = {fact.fact_id for fact in request.facts}
        if not set(draft.included_fact_ids).issubset(allowed):
            raise ValueError("draft referenced an unverified fact")

    @staticmethod
    def _render(
        request: GroundedResponseRequest,
        tone: ResponseTone,
        fact_ids: Sequence[str],
        *,
        used_model: bool,
    ) -> GroundedResponseResult:
        base = _TEMPLATES.get(request.template_id, _TEMPLATES["GENERIC_UPDATE"])
        allowed = {fact.fact_id: fact.safe_text for fact in request.facts}
        facts = " ".join(allowed[fact_id] for fact_id in fact_ids if fact_id in allowed)
        action = (
            f" 接下来请{request.required_user_action.strip()}。"
            if request.required_user_action
            else ""
        )
        text = f"{_PREFIXES[tone]}{base}{' ' + facts if facts else ''}{action}".strip()
        return GroundedResponseResult(
            text=text,
            template_id=request.template_id,
            message_outcome=request.message_outcome,
            required_user_action=request.required_user_action,
            used_model=used_model,
        )

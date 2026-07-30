"""Explicit, mutation-free DeepSeek smoke checks with sanitized evidence only."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.agent.enums import LLMRole
from app.agent.models import (
    AgentStateSummary,
    InterpretMessageInput,
    KnownIssueFields,
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from app.config import Settings
from app.domain.enums import WorkflowStage
from app.llm.errors import LLMProviderError, LLMProviderErrorCode
from app.llm.evaluation.artifacts import atomic_write_text
from app.llm.hybrid.pipeline import HybridInterpretationNode
from app.llm.hybrid.prompts import FactPromptRegistry
from app.llm.online.budget import ModelCallBudget, bind_model_call_budget
from app.llm.online.grounded import (
    GroundedFact,
    GroundedResponseDraft,
    GroundedResponseProvider,
    GroundedResponseRequest,
)
from app.llm.online.routing import DeepSeekStructuredRouter, RoutingPolicy
from app.llm.providers.deepseek import (
    DeepSeekConfig,
    DeepSeekStructuredInterpretationProvider,
)
from app.llm.validation.parser import StructuredInterpretationParser

LIVE_TEST_SWITCH = "FIXFLOW_ENABLE_LIVE_PROVIDER_TESTS"
SMOKE_SCHEMA_VERSION = "live-provider-smoke-v1"
_MESSAGES = (
    "厨房水管一直漏水",
    "卧室插座冒火花",
    "入户门锁打不开",
)


class SmokeObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: UUID
    provider: Literal["deepseek"]
    model: str
    capability: Literal["STRUCTURED_INTERPRETATION", "GROUNDED_RESPONSE"]
    prompt_version: str
    schema_version: str
    started_at: datetime
    completed_at: datetime
    latency_ms: int = Field(ge=0)
    result_status: Literal["SUCCEEDED", "FAILED", "FALLBACK"]
    error_code: str | None = None
    schema_valid: bool
    remaining_budget_ms: int = Field(ge=0)
    circuit_state: str


class LiveSmokeReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    report_schema_version: Literal["live-provider-smoke-v1"] = "live-provider-smoke-v1"
    run_id: UUID
    started_at: datetime
    completed_at: datetime
    explicit_switch: Literal[True] = True
    synthetic_inputs_only: Literal[True] = True
    business_mutation_count: Literal[0] = 0
    mcp_call_count: Literal[0] = 0
    flash_call_count: int = Field(ge=0)
    pro_call_count: int = Field(ge=0)
    grounded_call_count: int = Field(ge=0)
    mock_route_verified: bool
    deterministic_fallback_verified: bool
    passed: bool
    failed_rules: tuple[str, ...]
    observations: tuple[SmokeObservation, ...]


async def run_live_smoke(settings: Settings, *, output_path: Path) -> LiveSmokeReport:
    if not settings.enable_live_provider_tests:
        raise RuntimeError(f"live smoke is disabled; explicitly set {LIVE_TEST_SWITCH}=true")
    if not settings.llm_online_enabled or settings.llm_online_runtime_mode != "development":
        raise RuntimeError("live smoke requires the development-only online provider gate")
    if settings.deepseek_api_key is None:
        raise RuntimeError("DeepSeek credentials are unavailable")

    run_id = uuid4()
    started_at = datetime.now(UTC)
    observations: list[SmokeObservation] = []
    for model in ("deepseek-v4-flash", "deepseek-v4-pro"):
        provider = _provider(settings, model)
        node = HybridInterpretationNode(provider, model=model)
        try:
            for message in _MESSAGES:
                observations.append(
                    await _interpret_once(
                        run_id=run_id,
                        node=node,
                        model=model,
                        message=message,
                        budget_seconds=settings.llm_model_budget_seconds,
                    )
                )
        finally:
            await provider.close()

    grounded_provider = _provider(settings, "deepseek-v4-flash")
    grounded = GroundedResponseProvider(grounded_provider, model="deepseek-v4-flash")
    try:
        for facts in (
            (GroundedFact(fact_id="issue.type", safe_text="问题类型已确认。"),),
            (GroundedFact(fact_id="issue.location", safe_text="报修位置已确认。"),),
        ):
            observations.append(
                await _grounded_once(
                    run_id=run_id,
                    service=grounded,
                    facts=facts,
                    budget_seconds=settings.llm_model_budget_seconds,
                )
            )
    finally:
        await grounded_provider.close()

    route_ok = await _verify_mock_route()
    fallback_ok = await _verify_deterministic_fallback()
    flash_ok = any(
        item.model == "deepseek-v4-flash"
        and item.capability == "STRUCTURED_INTERPRETATION"
        and item.result_status == "SUCCEEDED"
        and item.schema_valid
        for item in observations
    )
    pro_ok = any(
        item.model == "deepseek-v4-pro"
        and item.capability == "STRUCTURED_INTERPRETATION"
        and item.result_status == "SUCCEEDED"
        and item.schema_valid
        for item in observations
    )
    grounded_ok = any(
        item.capability == "GROUNDED_RESPONSE"
        and item.result_status == "SUCCEEDED"
        and item.schema_valid
        for item in observations
    )
    failed = tuple(
        name
        for name, passed in (
            ("flash_structured", flash_ok),
            ("pro_structured", pro_ok),
            ("grounded_response", grounded_ok),
            ("mock_route", route_ok),
            ("deterministic_fallback", fallback_ok),
        )
        if not passed
    )
    report = LiveSmokeReport(
        run_id=run_id,
        started_at=started_at,
        completed_at=datetime.now(UTC),
        flash_call_count=sum(
            item.model == "deepseek-v4-flash" and item.capability == "STRUCTURED_INTERPRETATION"
            for item in observations
        ),
        pro_call_count=sum(
            item.model == "deepseek-v4-pro" and item.capability == "STRUCTURED_INTERPRETATION"
            for item in observations
        ),
        grounded_call_count=sum(item.capability == "GROUNDED_RESPONSE" for item in observations),
        mock_route_verified=route_ok,
        deterministic_fallback_verified=fallback_ok,
        passed=not failed,
        failed_rules=failed,
        observations=tuple(observations),
    )
    atomic_write_text(output_path, report.model_dump_json(indent=2) + "\n")
    return report


async def _interpret_once(
    *,
    run_id: UUID,
    node: HybridInterpretationNode,
    model: str,
    message: str,
    budget_seconds: float,
) -> SmokeObservation:
    budget = ModelCallBudget(budget_seconds)
    started = datetime.now(UTC)
    monotonic = time.monotonic()
    error_code: str | None = None
    schema_valid = False
    status: Literal["SUCCEEDED", "FAILED", "FALLBACK"] = "FAILED"
    prompt = FactPromptRegistry().resident_fact_extraction()
    try:
        with bind_model_call_budget(budget):
            result = await node(_input(message))
        schema_valid = result.metadata.schema_validated
        status = "SUCCEEDED"
    except Exception as exc:
        error_code = _safe_error_code(exc)
    completed = datetime.now(UTC)
    return SmokeObservation(
        run_id=run_id,
        provider="deepseek",
        model=model,
        capability="STRUCTURED_INTERPRETATION",
        prompt_version=prompt.prompt_version,
        schema_version=prompt.schema_version,
        started_at=started,
        completed_at=completed,
        latency_ms=int((time.monotonic() - monotonic) * 1000),
        result_status=status,
        error_code=error_code,
        schema_valid=schema_valid,
        remaining_budget_ms=int(budget.remaining_seconds * 1000),
        circuit_state="NOT_APPLICABLE",
    )


async def _grounded_once(
    *,
    run_id: UUID,
    service: GroundedResponseProvider,
    facts: tuple[GroundedFact, ...],
    budget_seconds: float,
) -> SmokeObservation:
    budget = ModelCallBudget(budget_seconds)
    started = datetime.now(UTC)
    monotonic = time.monotonic()
    with bind_model_call_budget(budget):
        result = await service.compose(
            GroundedResponseRequest(
                template_id="GENERIC_UPDATE",
                message_outcome="COMPLETED",
                required_user_action="确认下一步安排",
                facts=facts,
            )
        )
    completed = datetime.now(UTC)
    return SmokeObservation(
        run_id=run_id,
        provider="deepseek",
        model="deepseek-v4-flash",
        capability="GROUNDED_RESPONSE",
        prompt_version="1.0.0",
        schema_version="grounded-response-draft-v1",
        started_at=started,
        completed_at=completed,
        latency_ms=int((time.monotonic() - monotonic) * 1000),
        result_status="SUCCEEDED" if result.used_model else "FALLBACK",
        error_code=None if result.used_model else "DETERMINISTIC_FALLBACK",
        schema_valid=result.used_model,
        remaining_budget_ms=int(budget.remaining_seconds * 1000),
        circuit_state="NOT_APPLICABLE",
    )


def _provider(settings: Settings, model: str) -> DeepSeekStructuredInterpretationProvider:
    assert settings.deepseek_api_key is not None
    prompt = FactPromptRegistry().resident_fact_extraction()
    timeout = min(settings.deepseek_request_timeout_seconds, settings.llm_model_budget_seconds)
    return DeepSeekStructuredInterpretationProvider(
        prompt=prompt,
        parser=StructuredInterpretationParser(max_response_bytes=settings.llm_max_response_bytes),
        config=DeepSeekConfig(
            model=model,
            base_url=settings.deepseek_base_url,
            thinking_mode="disabled",
            temperature=settings.deepseek_temperature,
            top_p=settings.deepseek_top_p,
            max_tokens=settings.deepseek_max_tokens,
            request_timeout_seconds=timeout,
            total_timeout_seconds=settings.llm_model_budget_seconds,
            max_attempts=1,
            max_concurrency=1,
        ),
        api_key=settings.deepseek_api_key.get_secret_value(),
    )


def _input(message: str) -> InterpretMessageInput:
    return InterpretMessageInput(
        current_user_message=message,
        recent_conversation_messages=(),
        current_state_summary=AgentStateSummary(intent_version=1),
        current_workflow_stage=WorkflowStage.INTAKE,
        known_issue_fields=KnownIssueFields(),
        missing_fields=(),
        reference_time=datetime(2026, 7, 30, 9, tzinfo=UTC),
        timezone_name="UTC",
    )


def _safe_error_code(exc: Exception) -> str:
    candidate: BaseException | None = exc
    while candidate is not None:
        if isinstance(candidate, LLMProviderError):
            return candidate.code.value
        candidate = candidate.__cause__
    return type(exc).__name__.upper()


class _FaultProvider:
    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        del messages, response_model, model_config
        raise LLMProviderError(
            LLMProviderErrorCode.TIMEOUT,
            provider="deepseek",
            model="deepseek-v4-flash",
            retryable=True,
        )

    async def generate_response(
        self, *, messages: Sequence[LLMMessage], model_config: LLMRequestConfig
    ) -> TextLLMResult:
        del messages, model_config
        raise AssertionError

    async def health_check(self) -> bool:
        return False


class _DraftProvider:
    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        del messages, model_config
        payload = response_model.model_validate(
            {
                "template_id": "GENERIC_UPDATE",
                "tone": "CONCISE",
                "included_fact_ids": [],
            }
        )
        return StructuredLLMResult(
            payload=payload.model_dump(mode="json"),
            provider="deepseek",
            model="deepseek-v4-pro",
            prompt_name="mock_route",
            prompt_version="1.0.0",
        )

    async def generate_response(
        self, *, messages: Sequence[LLMMessage], model_config: LLMRequestConfig
    ) -> TextLLMResult:
        del messages, model_config
        raise AssertionError

    async def health_check(self) -> bool:
        return True


async def _verify_mock_route() -> bool:
    router = DeepSeekStructuredRouter(
        flash=_FaultProvider(),
        pro=_DraftProvider(),
        policy=RoutingPolicy(
            flash_transport_attempts=1,
            pro_transport_attempts=1,
            retry_delay_seconds=0,
        ),
    )
    result = await router.generate_structured(
        messages=(LLMMessage(role=LLMRole.SYSTEM, content="mock route"),),
        response_model=GroundedResponseDraft,
        model_config=LLMRequestConfig(
            model="deepseek-v4-flash",
            prompt_name="mock_route",
            prompt_version="1.0.0",
        ),
    )
    return result.model == "deepseek-v4-pro" and [item.phase for item in router.observations] == [
        "flash",
        "pro",
    ]


async def _verify_deterministic_fallback() -> bool:
    result = await GroundedResponseProvider(_FaultProvider(), model="deepseek-v4-flash").compose(
        GroundedResponseRequest(
            template_id="GENERIC_UPDATE",
            message_outcome="COMPLETED",
        )
    )
    return not result.used_model and result.message_outcome == "COMPLETED"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fixflow-live-provider-smoke")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    settings = Settings(
        llm_provider="deepseek",
        llm_online_enabled=True,
        llm_online_runtime_mode="development",
    )
    report = asyncio.run(run_live_smoke(settings, output_path=args.output))
    print(
        json.dumps(
            {
                "run_id": str(report.run_id),
                "passed": report.passed,
                "failed_rules": report.failed_rules,
                "flash_calls": report.flash_call_count,
                "pro_calls": report.pro_call_count,
                "grounded_calls": report.grounded_call_count,
                "artifact": str(args.output),
            },
            sort_keys=True,
        )
    )
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())

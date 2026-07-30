"""Bounded Flash-first structured routing with one repair and Pro fallback."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from pydantic import BaseModel

from app.agent.enums import LLMRole
from app.agent.models import (
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from app.agent.ports import LLMProvider
from app.llm.errors import (
    FAIL_FAST_ERROR_CODES,
    TRANSPORT_ERROR_CODES,
    LLMProviderError,
    LLMProviderErrorCode,
)
from app.llm.online.budget import ModelCallBudget, current_model_call_budget
from app.llm.online.circuit import CircuitBreaker, CircuitBreakerRegistry

Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    flash_transport_attempts: int = 2
    pro_transport_attempts: int = 1
    retry_delay_seconds: float = 0.25
    per_call_timeout_seconds: float = 20


@dataclass(frozen=True, slots=True)
class RoutingObservation:
    provider: str
    model: str
    phase: str
    success: bool
    error_code: LLMProviderErrorCode | None
    latency_ms: int = 0
    remaining_budget_seconds: float | None = None
    circuit_state: str | None = None


class ProviderExhaustedError(LLMProviderError):
    """All authorized online routes failed inside the shared request budget."""


class DeepSeekStructuredRouter:
    """No-tool structured provider. It owns retries; child adapters must retry once."""

    provider_name = "deepseek"

    def __init__(
        self,
        *,
        flash: LLMProvider,
        pro: LLMProvider,
        flash_model: str = "deepseek-v4-flash",
        pro_model: str = "deepseek-v4-pro",
        policy: RoutingPolicy | None = None,
        circuits: CircuitBreakerRegistry | None = None,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        self._flash = flash
        self._pro = pro
        self._flash_model = flash_model
        self._pro_model = pro_model
        self._policy = policy or RoutingPolicy()
        self._circuits = circuits or CircuitBreakerRegistry()
        self._flash_circuit = self._circuits.get(self.provider_name, flash_model)
        self._pro_circuit = self._circuits.get(self.provider_name, pro_model)
        self._sleeper = sleeper
        self.observations: list[RoutingObservation] = []

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        budget = current_model_call_budget() or ModelCallBudget()
        last_error: LLMProviderError | None = None
        try:
            result, schema_error = await self._route_model(
                provider=self._flash,
                model=self._flash_model,
                circuit=self._flash_circuit,
                messages=messages,
                response_model=response_model,
                model_config=model_config,
                transport_attempts=self._policy.flash_transport_attempts,
                budget=budget,
                phase="flash",
            )
            if result is not None:
                return result
            assert schema_error is not None
            last_error = schema_error
            repaired = self._repair_messages(messages, response_model, schema_error)
            try:
                return await self._single_call(
                    provider=self._flash,
                    model=self._flash_model,
                    circuit=self._flash_circuit,
                    messages=repaired,
                    response_model=response_model,
                    model_config=model_config,
                    budget=budget,
                    phase="flash_schema_repair",
                )
            except LLMProviderError as exc:
                if exc.code in FAIL_FAST_ERROR_CODES:
                    raise
                last_error = exc
        except LLMProviderError as exc:
            if exc.code in FAIL_FAST_ERROR_CODES:
                raise
            last_error = exc

        try:
            result, schema_error = await self._route_model(
                provider=self._pro,
                model=self._pro_model,
                circuit=self._pro_circuit,
                messages=messages,
                response_model=response_model,
                model_config=model_config,
                transport_attempts=self._policy.pro_transport_attempts,
                budget=budget,
                phase="pro",
            )
            if result is not None:
                return result
            assert schema_error is not None
            last_error = schema_error
        except LLMProviderError as exc:
            if exc.code in FAIL_FAST_ERROR_CODES:
                raise
            last_error = exc
        assert last_error is not None
        raise ProviderExhaustedError(
            last_error.code,
            provider=last_error.provider,
            model=last_error.model,
            retryable=False,
            request_id=last_error.request_id,
            attempt_count=last_error.attempt_count,
            safe_detail="all authorized structured providers were exhausted",
            cause_type=last_error.cause_type,
            schema_error_summary=last_error.schema_error_summary,
        )

    async def _route_model(
        self,
        *,
        provider: LLMProvider,
        model: str,
        circuit: CircuitBreaker,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
        transport_attempts: int,
        budget: ModelCallBudget,
        phase: str,
    ) -> tuple[StructuredLLMResult | None, LLMProviderError | None]:
        last_error: LLMProviderError | None = None
        for attempt in range(transport_attempts):
            try:
                return (
                    await self._single_call(
                        provider=provider,
                        model=model,
                        circuit=circuit,
                        messages=messages,
                        response_model=response_model,
                        model_config=model_config,
                        budget=budget,
                        phase=phase,
                    ),
                    None,
                )
            except LLMProviderError as exc:
                last_error = exc
                if exc.code is LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED:
                    return None, exc
                if exc.code not in TRANSPORT_ERROR_CODES or attempt + 1 >= transport_attempts:
                    raise
                delay = min(self._policy.retry_delay_seconds, budget.remaining_seconds)
                if delay <= 0:
                    budget.timeout_seconds(provider=self.provider_name, model=model)
                await self._sleeper(delay)
        assert last_error is not None
        raise last_error

    async def _single_call(
        self,
        *,
        provider: LLMProvider,
        model: str,
        circuit: CircuitBreaker,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
        budget: ModelCallBudget,
        phase: str,
    ) -> StructuredLLMResult:
        await circuit.before_call()
        timeout = budget.timeout_seconds(
            provider=self.provider_name,
            model=model,
            requested_seconds=self._policy.per_call_timeout_seconds,
        )
        routed_config = model_config.model_copy(update={"model": model})
        try:
            async with asyncio.timeout(timeout):
                result = await provider.generate_structured(
                    messages=messages,
                    response_model=response_model,
                    model_config=routed_config,
                )
        except TimeoutError as exc:
            error = LLMProviderError(
                LLMProviderErrorCode.TIMEOUT,
                provider=self.provider_name,
                model=model,
                retryable=True,
                safe_detail="structured provider timed out",
                cause_type=type(exc).__name__,
            )
            await circuit.record_transport_failure()
            self._observe(model, phase, error, circuit, budget)
            raise error from exc
        except LLMProviderError as exc:
            if exc.code in TRANSPORT_ERROR_CODES:
                await circuit.record_transport_failure()
            else:
                await circuit.record_non_transport_failure()
            self._observe(model, phase, exc, circuit, budget)
            raise
        await circuit.record_success()
        self.observations.append(
            RoutingObservation(
                self.provider_name,
                model,
                phase,
                True,
                None,
                result.latency_ms or 0,
                budget.remaining_seconds,
                circuit.snapshot().state.value,
            )
        )
        return result

    def _observe(
        self,
        model: str,
        phase: str,
        error: LLMProviderError,
        circuit: CircuitBreaker,
        budget: ModelCallBudget,
    ) -> None:
        self.observations.append(
            RoutingObservation(
                self.provider_name,
                model,
                phase,
                False,
                error.code,
                0,
                budget.remaining_seconds,
                circuit.snapshot().state.value,
            )
        )

    @staticmethod
    def _repair_messages(
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        error: LLMProviderError,
    ) -> tuple[LLMMessage, ...]:
        schema = json.dumps(
            response_model.model_json_schema(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        summary = error.schema_error_summary or "strict schema validation failed"
        repair = LLMMessage(
            role=LLMRole.SYSTEM,
            content=(
                "Repair the previous answer once. Return JSON only; do not add fields. "
                f"Validation summary: {summary}. Required schema: {schema}"
            )[:12000],
        )
        return (*messages, repair)

    async def generate_response(
        self,
        *,
        messages: Sequence[LLMMessage],
        model_config: LLMRequestConfig,
    ) -> TextLLMResult:
        del messages, model_config
        raise LLMProviderError(
            LLMProviderErrorCode.REQUEST_REJECTED,
            provider=self.provider_name,
            model=self._flash_model,
            retryable=False,
            safe_detail="free-text generation is outside this structured router",
        )

    async def health_check(self) -> bool:
        return await self._flash.health_check() and await self._pro.health_check()

    async def close(self) -> None:
        for provider in (self._flash, self._pro):
            close = getattr(provider, "close", None)
            if close is not None:
                await close()

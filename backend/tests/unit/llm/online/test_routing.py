from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Sequence

import pytest
from app.agent.enums import LLMRole
from app.agent.models import (
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from app.llm.errors import LLMProviderError, LLMProviderErrorCode
from app.llm.online.budget import ModelCallBudget, bind_model_call_budget
from app.llm.online.circuit import CircuitBreaker, CircuitState
from app.llm.online.routing import (
    DeepSeekStructuredRouter,
    ProviderExhaustedError,
    RoutingPolicy,
)
from pydantic import BaseModel, ConfigDict


class StrictPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


def _result(model: str, value: str = "ok") -> StructuredLLMResult:
    return StructuredLLMResult(
        payload={"value": value},
        provider="deepseek",
        model=model,
        prompt_name="facts",
        prompt_version="2.0.0",
    )


def _error(code: LLMProviderErrorCode, model: str = "deepseek-v4-flash") -> LLMProviderError:
    return LLMProviderError(
        code,
        provider="deepseek",
        model=model,
        retryable=code
        in {
            LLMProviderErrorCode.TIMEOUT,
            LLMProviderErrorCode.RATE_LIMITED,
            LLMProviderErrorCode.CONNECTION_FAILED,
            LLMProviderErrorCode.UPSTREAM_5XX,
        },
        schema_error_summary="value is required"
        if code is LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED
        else None,
    )


class FakeProvider:
    def __init__(self, *outcomes: StructuredLLMResult | Exception) -> None:
        self.outcomes = deque(outcomes)
        self.messages: list[tuple[LLMMessage, ...]] = []

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        del response_model, model_config
        self.messages.append(tuple(messages))
        outcome = self.outcomes.popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def generate_response(
        self,
        *,
        messages: Sequence[LLMMessage],
        model_config: LLMRequestConfig,
    ) -> TextLLMResult:
        raise AssertionError("free-text path must not be used")

    async def health_check(self) -> bool:
        return True


def _router(
    flash: FakeProvider,
    pro: FakeProvider,
    *,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> DeepSeekStructuredRouter:
    return DeepSeekStructuredRouter(
        flash=flash,
        pro=pro,
        policy=RoutingPolicy(retry_delay_seconds=0, per_call_timeout_seconds=20),
        sleeper=sleeper,
    )


async def _call(router: DeepSeekStructuredRouter) -> StructuredLLMResult:
    return await router.generate_structured(
        messages=(LLMMessage(role=LLMRole.USER, content="bounded"),),
        response_model=StrictPayload,
        model_config=LLMRequestConfig(
            model="ignored",
            prompt_name="facts",
            prompt_version="2.0.0",
        ),
    )


@pytest.mark.asyncio
async def test_flash_success_never_calls_pro() -> None:
    flash = FakeProvider(_result("deepseek-v4-flash"))
    pro = FakeProvider()
    assert (await _call(_router(flash, pro))).model == "deepseek-v4-flash"
    assert not pro.messages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        LLMProviderErrorCode.TIMEOUT,
        LLMProviderErrorCode.RATE_LIMITED,
        LLMProviderErrorCode.CONNECTION_FAILED,
        LLMProviderErrorCode.UPSTREAM_5XX,
    ],
)
async def test_flash_transport_failure_retries_once(code: LLMProviderErrorCode) -> None:
    flash = FakeProvider(_error(code), _result("deepseek-v4-flash"))
    pro = FakeProvider()
    await _call(_router(flash, pro))
    assert len(flash.messages) == 2
    assert not pro.messages


@pytest.mark.asyncio
async def test_two_flash_transport_failures_fall_back_to_pro() -> None:
    flash = FakeProvider(
        _error(LLMProviderErrorCode.TIMEOUT),
        _error(LLMProviderErrorCode.CONNECTION_FAILED),
    )
    pro = FakeProvider(_result("deepseek-v4-pro"))
    assert (await _call(_router(flash, pro))).model == "deepseek-v4-pro"


@pytest.mark.asyncio
async def test_flash_only_runtime_exhausts_without_contacting_another_model() -> None:
    flash = FakeProvider(
        _error(LLMProviderErrorCode.TIMEOUT),
        _error(LLMProviderErrorCode.CONNECTION_FAILED),
    )
    router = DeepSeekStructuredRouter(
        flash=flash,
        policy=RoutingPolicy(retry_delay_seconds=0, per_call_timeout_seconds=20),
    )

    with pytest.raises(ProviderExhaustedError) as caught:
        await _call(router)

    assert caught.value.model == "deepseek-v4-flash"
    assert [item.phase for item in router.observations] == ["flash", "flash"]


@pytest.mark.asyncio
async def test_schema_failure_gets_exactly_one_controlled_repair() -> None:
    flash = FakeProvider(
        _error(LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED),
        _result("deepseek-v4-flash", "repaired"),
    )
    pro = FakeProvider()
    await _call(_router(flash, pro))
    assert len(flash.messages) == 2
    assert "Return JSON only" in flash.messages[1][-1].content
    assert "value is required" in flash.messages[1][-1].content
    assert not pro.messages


@pytest.mark.asyncio
async def test_failed_schema_repair_uses_pro_without_repairing_pro() -> None:
    flash = FakeProvider(
        _error(LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED),
        _error(LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED),
    )
    pro = FakeProvider(_result("deepseek-v4-pro"))
    assert (await _call(_router(flash, pro))).model == "deepseek-v4-pro"
    assert len(pro.messages) == 1


@pytest.mark.asyncio
async def test_pro_schema_failure_becomes_provider_exhausted() -> None:
    flash = FakeProvider(
        _error(LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED),
        _error(LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED),
    )
    pro = FakeProvider(_error(LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED, "deepseek-v4-pro"))
    with pytest.raises(ProviderExhaustedError):
        await _call(_router(flash, pro))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code",
    [
        LLMProviderErrorCode.AUTHENTICATION_FAILED,
        LLMProviderErrorCode.INVALID_CONFIGURATION,
        LLMProviderErrorCode.CONTENT_POLICY_BLOCKED,
    ],
)
async def test_fail_fast_errors_never_retry_or_fall_back(code: LLMProviderErrorCode) -> None:
    flash = FakeProvider(_error(code))
    pro = FakeProvider()
    with pytest.raises(LLMProviderError, match="structured interpretation failed"):
        await _call(_router(flash, pro))
    assert len(flash.messages) == 1
    assert not pro.messages


@pytest.mark.asyncio
async def test_shared_budget_blocks_later_call_without_contacting_pro() -> None:
    now = [0.0]
    budget = ModelCallBudget(1, clock=lambda: now[0])

    async def advance(_: float) -> None:
        now[0] = 1.1

    flash = FakeProvider(_error(LLMProviderErrorCode.TIMEOUT))
    pro = FakeProvider()
    with bind_model_call_budget(budget), pytest.raises(ProviderExhaustedError) as caught:
        await _call(_router(flash, pro, sleeper=advance))
    assert caught.value.code is LLMProviderErrorCode.BUDGET_EXHAUSTED
    assert not pro.messages


@pytest.mark.asyncio
async def test_circuit_half_open_allows_one_probe_and_closes_on_success() -> None:
    now = [0.0]
    breaker = CircuitBreaker(
        provider="deepseek",
        model="flash",
        failure_threshold=1,
        recovery_seconds=5,
        clock=lambda: now[0],
    )
    await breaker.record_transport_failure()
    with pytest.raises(LLMProviderError) as opened:
        await breaker.before_call()
    assert opened.value.code is LLMProviderErrorCode.CIRCUIT_OPEN
    now[0] = 5
    await breaker.before_call()
    with pytest.raises(LLMProviderError):
        await breaker.before_call()
    await breaker.record_success()
    assert breaker.snapshot().state is CircuitState.CLOSED


@pytest.mark.asyncio
async def test_half_open_concurrency_allows_exactly_one_probe() -> None:
    now = [0.0]
    breaker = CircuitBreaker(
        provider="deepseek",
        model="flash",
        failure_threshold=1,
        recovery_seconds=1,
        clock=lambda: now[0],
    )
    await breaker.record_transport_failure()
    now[0] = 1
    results = await asyncio.gather(
        breaker.before_call(),
        breaker.before_call(),
        return_exceptions=True,
    )
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, LLMProviderError) for result in results) == 1


@pytest.mark.asyncio
async def test_flash_and_pro_circuit_state_is_independent() -> None:
    flash = FakeProvider(
        _error(LLMProviderErrorCode.TIMEOUT),
        _error(LLMProviderErrorCode.TIMEOUT),
    )
    pro = FakeProvider(_result("deepseek-v4-pro"))
    router = _router(flash, pro)
    await _call(router)
    assert router._flash_circuit.snapshot().state is CircuitState.OPEN
    assert router._pro_circuit is not None
    assert router._pro_circuit.snapshot().state is CircuitState.CLOSED

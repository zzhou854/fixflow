"""Official Z.AI SDK adapter for GLM structured interpretation only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

import zai
from pydantic import BaseModel
from zai import ZaiClient
from zai.core._errors import APIConnectionError

from app.agent.models import (
    InterpretMessageOutput,
    LLMMessage,
    LLMRequestConfig,
    StructuredLLMResult,
    TextLLMResult,
)
from app.agent_runtime.execution_context import current_execution_context
from app.infrastructure.database.models.observability import TraceSource
from app.llm.errors import LLMProviderError, LLMProviderErrorCode
from app.llm.prompts.registry import PromptDefinition
from app.llm.validation.parser import StructuredInterpretationParser
from app.trace.models import TracePayload

Sleeper = Callable[[float], Awaitable[None]]


class CompletionCreate(Protocol):
    def __call__(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class ZaiGLMConfig:
    model: str = "glm-5.1"
    base_url: str = "https://open.bigmodel.cn/api/paas/v4/"
    thinking_mode: str = "disabled"
    temperature: float = 0.1
    top_p: float = 0.8
    max_tokens: int = 1600
    request_timeout_seconds: float = 20.0
    total_timeout_seconds: float = 30.0
    max_attempts: int = 3
    retry_initial_delay_seconds: float = 0.5
    retry_max_delay_seconds: float = 4.0
    max_concurrency: int = 8


class ZaiGLMStructuredInterpretationProvider:
    """One bounded structured call; no tools, text generation, or business decisions."""

    provider_name = "zai"

    def __init__(
        self,
        *,
        prompt: PromptDefinition,
        parser: StructuredInterpretationParser,
        config: ZaiGLMConfig,
        api_key: str | None = None,
        completion_create: CompletionCreate | None = None,
        close_client: Callable[[], None] | None = None,
        sleeper: Sleeper = asyncio.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        self._prompt = prompt
        self._parser = parser
        self._config = config
        self._sleeper = sleeper
        self._random: random.Random = random_source or random.Random()
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._client: ZaiClient | None = None
        if completion_create is None:
            if not api_key:
                raise ValueError("api_key is required for the Z.AI provider")
            self._client = ZaiClient(
                api_key=api_key,
                base_url=config.base_url,
                timeout=config.request_timeout_seconds,
                max_retries=0,
            )
            completion_create = cast(CompletionCreate, self._client.chat.completions.create)
            close_client = self._client.close
        self._create = completion_create
        self._close_client = close_client

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        if response_model is not InterpretMessageOutput:
            raise ValueError("GLM structured provider supports only InterpretMessageOutput")
        encoded_messages = [
            {"role": message.role.value.lower(), "content": message.content} for message in messages
        ]
        input_hash = hashlib.sha256(
            json.dumps(
                encoded_messages,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        started = time.monotonic()
        await self._trace(
            "llm_interpretation_started",
            input_hash=input_hash,
            attempt_count=1,
        )
        try:
            async with asyncio.timeout(self._config.total_timeout_seconds):
                return await self._attempts(encoded_messages, model_config, input_hash, started)
        except TimeoutError as exc:
            error = self._error(
                LLMProviderErrorCode.TIMEOUT,
                retryable=False,
                attempt_count=self._config.max_attempts,
                cause=exc,
            )
            await self._trace_failure(error, input_hash, started)
            raise error from exc

    async def _attempts(
        self,
        messages: list[dict[str, str]],
        model_config: LLMRequestConfig,
        input_hash: str,
        started: float,
    ) -> StructuredLLMResult:
        last_error: LLMProviderError | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                response = await self._call_sync(messages)
                result = self._response_result(
                    response,
                    model_config=model_config,
                    attempt=attempt,
                    latency_ms=int((time.monotonic() - started) * 1000),
                )
                await self._trace_success(result, input_hash)
                return result
            except asyncio.CancelledError:
                raise
            except LLMProviderError as exc:
                error = exc
            except TimeoutError as exc:
                error = self._error(
                    LLMProviderErrorCode.TIMEOUT,
                    retryable=True,
                    attempt_count=attempt,
                    cause=exc,
                )
            except Exception as exc:  # SDK classes are normalized here only.
                error = self._map_sdk_error(exc, attempt)
            last_error = error
            if not error.retryable or attempt >= self._config.max_attempts:
                if error.retryable:
                    error = LLMProviderError(
                        error.code,
                        provider=error.provider,
                        model=error.model,
                        retryable=False,
                        request_id=error.request_id,
                        retry_after_seconds=error.retry_after_seconds,
                        attempt_count=attempt,
                        safe_detail=error.safe_detail,
                        cause_type=error.cause_type,
                    )
                await self._trace_failure(error, input_hash, started)
                raise error
            await self._trace(
                "llm_interpretation_retried",
                input_hash=input_hash,
                attempt_count=attempt,
                error=error,
            )
            await self._sleeper(self._retry_delay(attempt, error.retry_after_seconds))
        assert last_error is not None
        raise last_error

    def _response_result(
        self,
        response: object,
        *,
        model_config: LLMRequestConfig,
        attempt: int,
        latency_ms: int,
    ) -> StructuredLLMResult:
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise self._error(
                LLMProviderErrorCode.INVALID_RESPONSE,
                retryable=False,
                attempt_count=attempt,
            )
        choice = choices[0]
        message = getattr(choice, "message", None)
        interpretation = self._parser.parse(
            getattr(message, "content", None),
            provider=self.provider_name,
            model=self._config.model,
        )
        usage = getattr(response, "usage", None)
        input_tokens = self._nonnegative(getattr(usage, "prompt_tokens", None))
        output_tokens = self._nonnegative(getattr(usage, "completion_tokens", None))
        total_tokens = self._nonnegative(getattr(usage, "total_tokens", None))
        return StructuredLLMResult(
            payload=interpretation.model_dump(mode="json", exclude_none=True),
            provider=self.provider_name,
            model=self._config.model,
            prompt_name=model_config.prompt_name,
            prompt_version=model_config.prompt_version,
            prompt_hash=self._prompt.prompt_hash,
            schema_version=self._prompt.schema_version,
            request_id=self._bounded(getattr(response, "request_id", None), 200),
            finish_reason=self._bounded(getattr(choice, "finish_reason", None), 100),
            latency_ms=latency_ms,
            attempt_count=attempt,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            thinking_mode=self._config.thinking_mode,
        )

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
            model=self._config.model,
            retryable=False,
            safe_detail="online free-text generation is outside Task 13",
        )

    async def health_check(self) -> bool:
        # Startup validation is local; it must not spend tokens or require a network probe.
        return self._create is not None

    async def close(self) -> None:
        if self._close_client is not None:
            await asyncio.to_thread(self._close_client)

    async def _call_sync(self, messages: list[dict[str, str]]) -> object:
        # A timed-out sync SDK call cannot be force-cancelled. Keep its semaphore
        # permit until the worker thread really exits so timeouts cannot defeat
        # the configured concurrency bound.
        await self._semaphore.acquire()
        task = asyncio.create_task(
            asyncio.to_thread(
                self._create,
                model=self._config.model,
                messages=messages,
                response_format={"type": "json_object"},
                thinking={"type": self._config.thinking_mode},
                stream=False,
                temperature=self._config.temperature,
                top_p=self._config.top_p,
                max_tokens=self._config.max_tokens,
                timeout=self._config.request_timeout_seconds,
            )
        )
        task.add_done_callback(lambda _: self._semaphore.release())
        return await asyncio.wait_for(
            asyncio.shield(task),
            timeout=self._config.request_timeout_seconds,
        )

    def _map_sdk_error(self, exc: Exception, attempt: int) -> LLMProviderError:
        status = getattr(exc, "status_code", None)
        if isinstance(exc, zai.core.APIAuthenticationError) or status == 401:
            code, retryable = LLMProviderErrorCode.AUTHENTICATION_FAILED, False
        elif status == 403:
            code, retryable = LLMProviderErrorCode.PERMISSION_DENIED, False
        elif isinstance(exc, zai.core.APIReachLimitError) or status == 429:
            code, retryable = LLMProviderErrorCode.RATE_LIMITED, True
        elif isinstance(exc, zai.core.APITimeoutError) or status == 408:
            code, retryable = LLMProviderErrorCode.TIMEOUT, True
        elif isinstance(exc, APIConnectionError):
            code, retryable = LLMProviderErrorCode.CONNECTION_FAILED, True
        elif status in {500, 502, 503, 504} or isinstance(
            exc, (zai.core.APIInternalError, zai.core.APIServerFlowExceedError)
        ):
            code, retryable = LLMProviderErrorCode.UPSTREAM_SERVER_ERROR, True
        elif status == 400:
            upstream_code = str(getattr(exc, "code", "")).casefold()
            if "context" in upstream_code and "length" in upstream_code:
                code = LLMProviderErrorCode.CONTEXT_LENGTH_EXCEEDED
            elif "content" in upstream_code and (
                "filter" in upstream_code or "safety" in upstream_code
            ):
                code = LLMProviderErrorCode.CONTENT_FILTERED
            else:
                code = LLMProviderErrorCode.REQUEST_REJECTED
            retryable = False
        else:
            code, retryable = LLMProviderErrorCode.UNKNOWN_PROVIDER_ERROR, False
        return self._error(
            code,
            retryable=retryable,
            attempt_count=attempt,
            cause=exc,
            request_id=self._bounded(getattr(exc, "request_id", None), 200),
            retry_after_seconds=self._retry_after(exc),
        )

    def _error(
        self,
        code: LLMProviderErrorCode,
        *,
        retryable: bool,
        attempt_count: int,
        cause: BaseException | None = None,
        request_id: str | None = None,
        retry_after_seconds: float | None = None,
    ) -> LLMProviderError:
        return LLMProviderError(
            code,
            provider=self.provider_name,
            model=self._config.model,
            retryable=retryable,
            request_id=request_id,
            retry_after_seconds=retry_after_seconds,
            attempt_count=attempt_count,
            safe_detail="structured interpretation provider failed",
            cause_type=type(cause).__name__ if cause is not None else None,
        )

    def _retry_delay(self, attempt: int, retry_after: float | None) -> float:
        base = min(
            self._config.retry_max_delay_seconds,
            self._config.retry_initial_delay_seconds * (2 ** (attempt - 1)),
        )
        delay = max(base + self._random.uniform(0, base / 4 if base else 0), retry_after or 0)
        return float(min(delay, self._config.total_timeout_seconds))

    @staticmethod
    def _retry_after(exc: Exception) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is None:
            return None
        try:
            value = headers.get("retry-after")
            parsed = float(value)
        except (AttributeError, TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    async def _trace_success(self, result: StructuredLLMResult, input_hash: str) -> None:
        await self._trace(
            "llm_interpretation_succeeded",
            input_hash=input_hash,
            attempt_count=result.attempt_count,
            result=result,
        )

    async def _trace_failure(
        self,
        error: LLMProviderError,
        input_hash: str,
        started: float,
    ) -> None:
        await self._trace(
            "llm_interpretation_failed",
            input_hash=input_hash,
            attempt_count=error.attempt_count,
            error=error,
            latency_ms=int((time.monotonic() - started) * 1000),
        )

    async def _trace(
        self,
        event_type: str,
        *,
        input_hash: str,
        attempt_count: int,
        result: StructuredLLMResult | None = None,
        error: LLMProviderError | None = None,
        latency_ms: int | None = None,
    ) -> None:
        context = current_execution_context()
        if context is None or context.trace is None:
            return
        suffix = f"{attempt_count}:{event_type}"
        payload = TracePayload(
            provider=self.provider_name,
            model=self._config.model,
            prompt_id=self._prompt.prompt_id,
            prompt_version=self._prompt.prompt_version,
            prompt_hash=self._prompt.prompt_hash,
            schema_version=self._prompt.schema_version,
            input_hash=input_hash,
            thinking_mode=self._config.thinking_mode,
            latency_ms=result.latency_ms if result is not None else latency_ms,
            attempt_count=attempt_count,
            input_tokens=result.input_tokens if result is not None else None,
            output_tokens=result.output_tokens if result is not None else None,
            total_tokens=result.total_tokens if result is not None else None,
            finish_reason=result.finish_reason if result is not None else None,
            provider_request_id=result.request_id
            if result is not None
            else error.request_id
            if error
            else None,
            success=result is not None,
            error_code=error.code.value if error is not None else None,
            retryable=error.retryable if error is not None else None,
        )
        try:
            await context.trace.append_event(
                event_key=context.trace.event_key(context.run_id, event_type, suffix),
                run_id=context.run_id,
                thread_id=context.thread_id,
                trace_id=context.trace_id,
                source=TraceSource.AGENT,
                event_type=event_type,
                node_name="interpret",
                payload=payload,
                occurred_at=datetime.now(UTC),
            )
        except Exception:
            # Trace is audit evidence and never changes provider or business outcomes.
            return

    @staticmethod
    def _bounded(value: object, limit: int) -> str | None:
        return value[:limit] if isinstance(value, str) and value else None

    @staticmethod
    def _nonnegative(value: object) -> int | None:
        return value if isinstance(value, int) and value >= 0 else None

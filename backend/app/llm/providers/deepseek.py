"""DeepSeek OpenAI-compatible adapter for structured interpretation only."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel

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
SUPPORTED_DEEPSEEK_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})


@dataclass(frozen=True, slots=True)
class DeepSeekConfig:
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
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


class DeepSeekStructuredInterpretationProvider:
    """Bounded JSON-mode call with no tools and no business authority."""

    provider_name = "deepseek"

    def __init__(
        self,
        *,
        prompt: PromptDefinition,
        parser: StructuredInterpretationParser,
        config: DeepSeekConfig,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        sleeper: Sleeper = asyncio.sleep,
        random_source: random.Random | None = None,
    ) -> None:
        if config.model not in SUPPORTED_DEEPSEEK_MODELS:
            raise ValueError("DeepSeek provider supports deepseek-v4-flash and deepseek-v4-pro")
        if config.thinking_mode != "disabled":
            raise ValueError("DeepSeek structured interpretation requires disabled thinking")
        self._prompt = prompt
        self._parser = parser
        self._config = config
        self._sleeper = sleeper
        self._random = random_source or random.Random()
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._owns_client = client is None
        if client is None:
            if not api_key:
                raise ValueError("api_key is required for the DeepSeek provider")
            client = httpx.AsyncClient(
                base_url=config.base_url.rstrip("/") + "/",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(config.request_timeout_seconds),
            )
        self._client = client

    async def generate_structured(
        self,
        *,
        messages: Sequence[LLMMessage],
        response_model: type[BaseModel],
        model_config: LLMRequestConfig,
    ) -> StructuredLLMResult:
        if response_model is not InterpretMessageOutput:
            raise ValueError("DeepSeek provider supports only InterpretMessageOutput")
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
        await self._trace("llm_interpretation_started", input_hash=input_hash, attempt_count=1)
        try:
            async with asyncio.timeout(self._config.total_timeout_seconds):
                return await self._attempts(
                    encoded_messages,
                    model_config=model_config,
                    input_hash=input_hash,
                    started=started,
                )
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
        *,
        model_config: LLMRequestConfig,
        input_hash: str,
        started: float,
    ) -> StructuredLLMResult:
        last_error: LLMProviderError | None = None
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                response = await self._call(messages)
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
            except (httpx.TimeoutException, TimeoutError) as exc:
                error = self._error(
                    LLMProviderErrorCode.TIMEOUT,
                    retryable=True,
                    attempt_count=attempt,
                    cause=exc,
                )
            except httpx.NetworkError as exc:
                error = self._error(
                    LLMProviderErrorCode.CONNECTION_FAILED,
                    retryable=True,
                    attempt_count=attempt,
                    cause=exc,
                )
            except Exception as exc:
                error = self._error(
                    LLMProviderErrorCode.UNKNOWN_PROVIDER_ERROR,
                    retryable=False,
                    attempt_count=attempt,
                    cause=exc,
                )
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

    async def _call(self, messages: list[dict[str, str]]) -> httpx.Response:
        body: dict[str, object] = {
            "model": self._config.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "thinking": {"type": self._config.thinking_mode},
            "stream": False,
            "temperature": self._config.temperature,
            "top_p": self._config.top_p,
            "max_tokens": self._config.max_tokens,
        }
        async with self._semaphore:
            response = await self._client.post(
                "chat/completions",
                json=body,
                timeout=self._config.request_timeout_seconds,
            )
        if response.is_error:
            raise self._response_error(response)
        return response

    def _response_result(
        self,
        response: httpx.Response,
        *,
        model_config: LLMRequestConfig,
        attempt: int,
        latency_ms: int,
    ) -> StructuredLLMResult:
        try:
            payload = response.json()
        except ValueError as exc:
            raise self._error(
                LLMProviderErrorCode.INVALID_RESPONSE,
                retryable=False,
                attempt_count=attempt,
                cause=exc,
            ) from exc
        if not isinstance(payload, dict):
            raise self._error(
                LLMProviderErrorCode.INVALID_RESPONSE,
                retryable=False,
                attempt_count=attempt,
            )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise self._error(
                LLMProviderErrorCode.INVALID_RESPONSE,
                retryable=False,
                attempt_count=attempt,
            )
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            raise self._error(
                LLMProviderErrorCode.INVALID_RESPONSE,
                retryable=False,
                attempt_count=attempt,
            )
        tool_calls = message.get("tool_calls")
        transport_tool_call_count = (
            len(tool_calls) if isinstance(tool_calls, list) else int(tool_calls is not None)
        )
        interpretation = self._parser.parse(
            message.get("content"),
            provider=self.provider_name,
            model=self._config.model,
        )
        usage = payload.get("usage")
        usage_values = usage if isinstance(usage, dict) else {}
        return StructuredLLMResult(
            payload=interpretation.model_dump(mode="json", exclude_none=True),
            provider=self.provider_name,
            model=self._config.model,
            prompt_name=model_config.prompt_name,
            prompt_version=model_config.prompt_version,
            prompt_hash=self._prompt.prompt_hash,
            schema_version=self._prompt.schema_version,
            request_id=self._request_id(response, payload),
            finish_reason=self._bounded(choice.get("finish_reason"), 100),
            latency_ms=latency_ms,
            attempt_count=attempt,
            input_tokens=self._nonnegative(usage_values.get("prompt_tokens")),
            output_tokens=self._nonnegative(usage_values.get("completion_tokens")),
            total_tokens=self._nonnegative(usage_values.get("total_tokens")),
            thinking_mode=self._config.thinking_mode,
            transport_tool_call_count=transport_tool_call_count,
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
            safe_detail="online free-text generation is outside the structured provider boundary",
        )

    async def health_check(self) -> bool:
        # Deliberately local: startup must not spend tokens or probe user credentials.
        return not self._client.is_closed

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _response_error(self, response: httpx.Response) -> LLMProviderError:
        status = response.status_code
        if status == 401:
            code, retryable = LLMProviderErrorCode.AUTHENTICATION_FAILED, False
        elif status == 403:
            code, retryable = LLMProviderErrorCode.PERMISSION_DENIED, False
        elif status == 429:
            code, retryable = LLMProviderErrorCode.RATE_LIMITED, True
        elif status == 408:
            code, retryable = LLMProviderErrorCode.TIMEOUT, True
        elif status in {500, 502, 503, 504}:
            code, retryable = LLMProviderErrorCode.UPSTREAM_SERVER_ERROR, True
        elif status == 400:
            upstream_code = self._upstream_error_code(response)
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
            attempt_count=1,
            request_id=self._request_id(response),
            retry_after_seconds=self._retry_after(response),
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
    def _retry_after(response: httpx.Response) -> float | None:
        try:
            parsed = float(response.headers["retry-after"])
        except (KeyError, TypeError, ValueError):
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _upstream_error_code(response: httpx.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return ""
        if not isinstance(body, dict):
            return ""
        error = body.get("error")
        if not isinstance(error, dict):
            return ""
        return " ".join(str(error.get(key, "")).casefold() for key in ("code", "type", "message"))

    @staticmethod
    def _request_id(
        response: httpx.Response,
        payload: dict[str, object] | None = None,
    ) -> str | None:
        header = response.headers.get("x-request-id")
        body_id = payload.get("id") if payload is not None else None
        value = header or body_id
        return value[:200] if isinstance(value, str) and value else None

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
            provider_request_id=(
                result.request_id
                if result is not None
                else error.request_id
                if error is not None
                else None
            ),
            success=result is not None,
            error_code=error.code.value if error is not None else None,
            retryable=error.retryable if error is not None else None,
        )
        try:
            await context.trace.append_event(
                event_key=context.trace.event_key(
                    context.run_id,
                    event_type,
                    f"{attempt_count}:{event_type}",
                ),
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
            return

    @staticmethod
    def _bounded(value: object, limit: int) -> str | None:
        return value[:limit] if isinstance(value, str) and value else None

    @staticmethod
    def _nonnegative(value: object) -> int | None:
        return value if isinstance(value, int) and value >= 0 else None

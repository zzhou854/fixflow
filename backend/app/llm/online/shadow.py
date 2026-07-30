"""Zero-influence shadow node and sanitized durable evidence sink."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.models import InterpretationNodeResult, InterpretMessageInput
from app.agent_runtime.execution_context import current_execution_context
from app.infrastructure.database.models.online import (
    LLMShadowResultStatus,
    LLMShadowRun,
)
from app.llm.errors import LLMProviderError

InterpretationNode = Callable[[InterpretMessageInput], Awaitable[InterpretationNodeResult]]


@dataclass(frozen=True, slots=True)
class ShadowRunEvidence:
    source_run_id: UUID
    provider: str
    model: str
    prompt_version: str
    schema_version: str
    status: LLMShadowResultStatus
    latency_ms: int
    error_code: str | None
    structured_result_hash: str | None
    created_at: datetime


class SqlAlchemyShadowEvidenceSink:
    """Independent best-effort transaction; never participates in business truth."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def append(self, evidence: ShadowRunEvidence) -> None:
        async with self._sessions() as session, session.begin():
            session.add(
                LLMShadowRun(
                    source_run_id=evidence.source_run_id,
                    provider=evidence.provider,
                    model=evidence.model,
                    prompt_version=evidence.prompt_version,
                    schema_version=evidence.schema_version,
                    result_status=evidence.status,
                    latency_ms=evidence.latency_ms,
                    error_code=evidence.error_code,
                    structured_result_hash=evidence.structured_result_hash,
                    created_at=evidence.created_at,
                )
            )


class ShadowingInterpretationNode:
    """Returns the primary result immediately; shadow output cannot reach graph state."""

    def __init__(
        self,
        *,
        primary: InterpretationNode,
        shadow: InterpretationNode,
        sink: SqlAlchemyShadowEvidenceSink,
        provider: str,
        model: str,
        prompt_version: str,
        schema_version: str,
    ) -> None:
        self._primary = primary
        self._shadow = shadow
        self._sink = sink
        self._provider = provider
        self._model = model
        self._prompt_version = prompt_version
        self._schema_version = schema_version
        self._tasks: set[asyncio.Task[None]] = set()

    async def __call__(self, node_input: InterpretMessageInput) -> InterpretationNodeResult:
        result = await self._primary(node_input)
        context = current_execution_context()
        if context is not None:
            task = asyncio.create_task(
                self._observe(context.run_id, node_input),
                name="fixflow-structured-shadow",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return result

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        await self.drain()
        provider = getattr(self._shadow, "_provider", None)
        close = getattr(provider, "close", None)
        if close is not None:
            await close()

    async def _observe(self, source_run_id: UUID, node_input: InterpretMessageInput) -> None:
        started = time.monotonic()
        try:
            result = await self._shadow(node_input)
            canonical = json.dumps(
                result.interpretation.model_dump(mode="json", exclude_none=True),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            evidence = ShadowRunEvidence(
                source_run_id=source_run_id,
                provider=result.metadata.provider,
                model=result.metadata.model,
                prompt_version=result.metadata.prompt_version,
                schema_version=result.metadata.schema_version or self._schema_version,
                status=LLMShadowResultStatus.SUCCEEDED,
                latency_ms=int((time.monotonic() - started) * 1000),
                error_code=None,
                structured_result_hash=hashlib.sha256(canonical.encode()).hexdigest(),
                created_at=datetime.now(UTC),
            )
        except Exception as exc:
            code = exc.code.value if isinstance(exc, LLMProviderError) else type(exc).__name__
            evidence = ShadowRunEvidence(
                source_run_id=source_run_id,
                provider=self._provider,
                model=self._model,
                prompt_version=self._prompt_version,
                schema_version=self._schema_version,
                status=LLMShadowResultStatus.FAILED,
                latency_ms=int((time.monotonic() - started) * 1000),
                error_code=code[:80],
                structured_result_hash=None,
                created_at=datetime.now(UTC),
            )
        try:
            await self._sink.append(evidence)
        except Exception:
            return

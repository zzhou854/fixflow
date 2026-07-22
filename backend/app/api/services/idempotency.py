"""Bounded request replay protection for the single-process Task 9 API."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar, cast
from uuid import UUID

from app.api.errors import ApiError

ResultT = TypeVar("ResultT")


@dataclass(slots=True)
class _Execution[ResultT]:
    payload_hash: str
    task: asyncio.Future[ResultT]


class ApiIdempotencyStore:
    """Coalesce identical HTTP mutations without becoming a business fact source."""

    def __init__(self, *, max_entries: int = 1024) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[UUID, str, str], _Execution[Any]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def execute(
        self,
        *,
        caller_id: UUID,
        scope: str,
        key: str,
        payload: Mapping[str, object],
        operation: Callable[[], Awaitable[ResultT]],
        on_replay: Callable[[ResultT], Awaitable[None]] | None = None,
        on_conflict: Callable[[], Awaitable[None]] | None = None,
    ) -> ResultT:
        normalized_key = key.strip()
        if not normalized_key or len(normalized_key) > 128:
            raise ApiError(422, "VALIDATION_ERROR", "Idempotency-Key 格式不正确。")
        payload_hash = self.payload_fingerprint(payload)
        record_key = (caller_id, scope, normalized_key)
        replay = False
        async with self._lock:
            existing = self._entries.get(record_key)
            if existing is not None:
                if existing.payload_hash != payload_hash:
                    if on_conflict is not None:
                        await self._notify(on_conflict)
                    raise ApiError(
                        409,
                        "IDEMPOTENCY_CONFLICT",
                        "同一 Idempotency-Key 不能用于不同请求。",
                    )
                self._entries.move_to_end(record_key)
                task = existing.task
                replay = True
            else:
                self._evict_completed()
                task = asyncio.ensure_future(operation())
                self._entries[record_key] = _Execution(payload_hash, task)
        try:
            result = cast(ResultT, await asyncio.shield(task))
            if replay and on_replay is not None:
                await self._notify(lambda: on_replay(result))
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            async with self._lock:
                current = self._entries.get(record_key)
                if current is not None and current.task is task:
                    self._entries.pop(record_key, None)
            raise

    async def discard_completed(
        self,
        *,
        caller_id: UUID,
        scope: str,
        key: str,
        payload: Mapping[str, object],
    ) -> bool:
        """Release one completed API replay only after a formal NOT_COMMITTED verdict.

        The caller owns the reconciliation decision. This store only verifies
        that the exact caller, scope, key, and payload are being released.
        """

        normalized_key = key.strip()
        record_key = (caller_id, scope, normalized_key)
        payload_hash = self.payload_fingerprint(payload)
        async with self._lock:
            existing = self._entries.get(record_key)
            if (
                existing is None
                or existing.payload_hash != payload_hash
                or not existing.task.done()
            ):
                return False
            self._entries.pop(record_key, None)
            return True

    def _evict_completed(self) -> None:
        while len(self._entries) >= self._max_entries:
            candidate = next(
                (key for key, value in self._entries.items() if value.task.done()), None
            )
            if candidate is None:
                raise ApiError(503, "SERVICE_UNAVAILABLE", "请求去重服务繁忙。", retryable=True)
            self._entries.pop(candidate)

    @staticmethod
    def key_fingerprint(key: str) -> str:
        """A safe digest for audit records; raw HTTP keys are never persisted."""

        return hashlib.sha256(key.strip().encode()).hexdigest()

    @staticmethod
    def payload_fingerprint(payload: Mapping[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()

    @staticmethod
    async def _notify(callback: Callable[[], Awaitable[None]]) -> None:
        """Replay audit is observational and cannot invalidate a cached result."""

        try:
            await callback()
        except Exception:
            # The original mutation has already committed (or was already
            # cached). Trace failure must not turn a safe replay into a second
            # business execution.
            return

    async def close(self) -> None:
        async with self._lock:
            tasks = tuple(value.task for value in self._entries.values() if not value.task.done())
            self._entries.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

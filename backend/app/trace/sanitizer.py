"""Defence-in-depth sanitizer for persisted trace payloads."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from app.trace.models import TracePayload

SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "access_token",
        "refresh_token",
        "jwt",
        "password",
        "password_hash",
        "secret",
        "database_url",
        "api_key",
        "cookie",
        "set-cookie",
        "idempotency_key",
        "idempotency-key",
        "conversation_messages",
        "pending_operation",
        "checkpoint",
        "checkpoint_blob",
        "traceback",
        "stack_trace",
        "sql",
        "dsn",
        "mcp_request",
        "mcp_response",
        "provider_prompt",
        "prompt",
    }
)


class UnsafeTracePayload(ValueError):
    pass


class TraceSanitizer:
    def __init__(self, *, max_payload_bytes: int, max_string_length: int) -> None:
        if max_payload_bytes < 128 or max_string_length < 16:
            raise ValueError("trace limits are too small")
        self._max_payload_bytes = max_payload_bytes
        self._max_string_length = max_string_length

    def sanitize(self, payload: TracePayload) -> dict[str, object]:
        raw = payload.model_dump(mode="python", exclude_none=True)
        value = self._clean(raw)
        assert isinstance(value, dict)
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > self._max_payload_bytes:
            raise UnsafeTracePayload("trace payload exceeds configured byte limit")
        return value

    def inspect_untrusted(self, value: object) -> object:
        """Testable recursive guard; production callers persist only TracePayload."""

        return self._clean(value)

    def _clean(self, value: object) -> object:
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[: self._max_string_length]
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            if value.tzinfo is None or value.utcoffset() is None:
                raise UnsafeTracePayload("naive datetime is not allowed")
            return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if isinstance(value, Enum):
            return self._clean(value.value)
        if isinstance(value, Mapping):
            result: dict[str, object] = {}
            for key, item in value.items():
                normalized = str(key).casefold()
                if normalized in SENSITIVE_KEYS:
                    raise UnsafeTracePayload(f"sensitive trace key rejected: {normalized}")
                result[str(key)] = self._clean(item)
            return result
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self._clean(item) for item in value]
        raise UnsafeTracePayload(f"unsupported trace value type: {type(value).__name__}")

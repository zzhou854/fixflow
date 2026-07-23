"""Canonical JSON and integrity fingerprints for replay artifacts."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json", exclude_none=False))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    return value


def canonical_json(value: object) -> str:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_fingerprint(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def safe_request_fingerprint(value: BaseModel) -> str:
    """Hash a request after removing secrets and invocation-only correlation."""

    payload = value.model_dump(mode="json", exclude_none=True)
    for key in (
        "authorization",
        "idempotency_key",
        "trace_id",
        "run_id",
        "thread_id",
        "password",
        "secret",
        "api_key",
    ):
        payload.pop(key, None)
    return sha256_fingerprint(payload)

"""Shared transaction, idempotency, authorization, and history mechanics."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from app.application.errors import (
    ApplicationError,
    AuthorizationFailed,
    IdempotencyConflict,
)
from app.application.models import MutationMetadata, OperationResult
from app.application.ports import (
    AppointmentHistoryRecord,
    TicketHistoryRecord,
    UnitOfWork,
    UnitOfWorkFactory,
)
from app.domain.enums import ActorType
from app.domain.errors import DomainError
from app.domain.models import AppointmentSnapshot, TicketSnapshot

Handler = Callable[[UnitOfWork, str], Awaitable[OperationResult]]


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime values must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_json_value(item) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return value


def _request_hash(payload: dict[str, object]) -> str:
    semantic_payload = dict(payload)
    metadata = semantic_payload.get("metadata")
    if isinstance(metadata, dict):
        semantic_metadata = dict(metadata)
        semantic_metadata.pop("idempotency_key", None)
        semantic_metadata.pop("trace_id", None)
        semantic_metadata.pop("occurred_at", None)
        semantic_payload["metadata"] = semantic_metadata
    encoded = json.dumps(_json_value(semantic_payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _result_payload(result: OperationResult) -> dict[str, Any]:
    return {
        "code": result.code,
        "resource_version": result.resource_version,
        "data": _json_value(result.data),
    }


def _replay_result(
    resource_type: str | None,
    resource_id: UUID | None,
    payload: dict[str, Any] | None,
) -> OperationResult:
    stored = payload or {}
    return OperationResult(
        ok=True,
        code=str(stored.get("code", "OK")),
        resource_type=resource_type,
        resource_id=resource_id,
        resource_version=stored.get("resource_version"),
        replayed=True,
        data=dict(stored.get("data", {})),
    )


def _failure(error: DomainError | ApplicationError) -> OperationResult:
    return OperationResult(ok=False, code=error.code, data={"context": dict(error.context)})


class TransactionalService:
    """Small shared mechanics for concrete FixFlow mutation services."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._uow_factory = uow_factory
        self._id_factory = id_factory

    async def _execute(
        self,
        *,
        scope: str,
        metadata: MutationMetadata,
        payload: dict[str, object],
        handler: Handler,
    ) -> OperationResult:
        digest = _request_hash(payload)
        async with self._uow_factory() as uow:
            try:
                stored = await uow.idempotency.acquire(
                    scope=scope,
                    actor_type=metadata.actor_type,
                    actor_id=metadata.actor_id,
                    idempotency_key=metadata.idempotency_key,
                    request_hash=digest,
                )
                if stored.request_hash != digest:
                    raise IdempotencyConflict("idempotency_payload_conflict", scope=scope)
                if not stored.is_new:
                    if stored.execution_status != "SUCCEEDED":
                        raise IdempotencyConflict("idempotency_request_in_progress", scope=scope)
                    await uow.rollback()
                    return _replay_result(
                        stored.resource_type, stored.resource_id, stored.response_payload
                    )
                result = await handler(uow, digest)
                if not result.ok or result.resource_type is None or result.resource_id is None:
                    raise RuntimeError("successful handler must return a resource identity")
                await uow.flush()
                await uow.idempotency.succeed(
                    scope=scope,
                    actor_type=metadata.actor_type,
                    actor_id=metadata.actor_id,
                    idempotency_key=metadata.idempotency_key,
                    resource_type=result.resource_type,
                    resource_id=result.resource_id,
                    response_payload=_result_payload(result),
                )
                await uow.flush()
                await uow.commit()
                return result
            except (DomainError, ApplicationError) as exc:
                await uow.rollback()
                return _failure(exc)

    async def _authorize_ticket(
        self, uow: UnitOfWork, ticket: TicketSnapshot, metadata: MutationMetadata
    ) -> None:
        if metadata.actor_type is ActorType.RESIDENT:
            authorized = (
                metadata.actor_id == ticket.resident_id
                and await uow.tickets.resident_has_property(ticket.resident_id, ticket.property_id)
            )
            if not authorized:
                raise AuthorizationFailed("resident_not_authorized", ticket_id=ticket.ticket_id)
        elif metadata.actor_type is ActorType.OPERATOR:
            if not await uow.tickets.actor_is_operator(metadata.actor_id):
                raise AuthorizationFailed("operator_not_authorized")
        else:
            raise AuthorizationFailed("actor_not_allowed")

    @staticmethod
    def _ticket_history(
        before: TicketSnapshot,
        after: TicketSnapshot,
        action: str,
        metadata: MutationMetadata,
        *,
        reason_code: str | None = None,
        reason_text: str | None = None,
        evidence: tuple[str, ...] = (),
    ) -> TicketHistoryRecord:
        return TicketHistoryRecord(
            ticket_id=before.ticket_id,
            from_status=before.status,
            to_status=after.status,
            action=action,
            actor_type=metadata.actor_type,
            actor_id=metadata.actor_id,
            trace_id=metadata.trace_id,
            version_before=before.version,
            version_after=after.version,
            reason_code=reason_code,
            reason_text=reason_text,
            evidence=evidence,
        )

    @staticmethod
    def _appointment_history(
        before: AppointmentSnapshot,
        after: AppointmentSnapshot,
        metadata: MutationMetadata,
        *,
        reason_code: str | None = None,
        reason_text: str | None = None,
        evidence: tuple[str, ...] = (),
    ) -> AppointmentHistoryRecord:
        return AppointmentHistoryRecord(
            appointment_id=before.appointment_id,
            from_status=before.status,
            to_status=after.status,
            actor_type=metadata.actor_type,
            actor_id=metadata.actor_id,
            trace_id=metadata.trace_id,
            version_before=before.version,
            version_after=after.version,
            occurred_at=metadata.occurred_at,
            reason_code=reason_code,
            reason_text=reason_text,
            evidence=evidence,
        )

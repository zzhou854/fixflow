"""Closed, deterministic domain-event contracts for the transactional outbox."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid5

from app.application.models import MutationMetadata

_EVENT_NAMESPACE = UUID("26cffbaf-1bf9-58f2-a3af-b040874b491b")


class DomainEventType(StrEnum):
    TICKET_CREATED = "ticket.created"
    TICKET_STATUS_CHANGED = "ticket.status_changed"
    APPOINTMENT_BOOKED = "appointment.booked"
    APPOINTMENT_RESCHEDULED = "appointment.rescheduled"
    TICKET_ESCALATED = "ticket.escalated"


class AggregateType(StrEnum):
    TICKET = "repair_ticket"
    APPOINTMENT = "appointment"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_id: UUID
    event_key: str
    event_type: DomainEventType
    aggregate_type: AggregateType
    aggregate_id: UUID
    aggregate_version: int
    operation_id: UUID
    trace_id: UUID
    run_id: UUID | None
    thread_id: UUID | None
    actor_type: str
    actor_id: UUID
    occurred_at: datetime
    payload: dict[str, str | int | bool | None]


def operation_id_for(metadata: MutationMetadata, scope: str) -> UUID:
    """Return a retry-stable operation identity without using trace or time."""

    if metadata.operation_id is not None:
        return metadata.operation_id
    material = f"{scope}|{metadata.actor_type.value}|{metadata.actor_id}|{metadata.idempotency_key}"
    return uuid5(_EVENT_NAMESPACE, material)


def build_domain_event(
    *,
    event_type: DomainEventType,
    aggregate_type: AggregateType,
    aggregate_id: UUID,
    aggregate_version: int,
    metadata: MutationMetadata,
    scope: str,
    payload: dict[str, str | int | bool | None],
) -> DomainEvent:
    """Create a stable event identity from approved business identifiers."""

    operation_id = operation_id_for(metadata, scope)
    material = f"{event_type.value}|{aggregate_id}|{aggregate_version}|{operation_id}"
    event_key = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return DomainEvent(
        event_id=uuid5(_EVENT_NAMESPACE, event_key),
        event_key=event_key,
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        aggregate_version=aggregate_version,
        operation_id=operation_id,
        trace_id=metadata.trace_id,
        run_id=metadata.run_id,
        thread_id=metadata.thread_id,
        actor_type=metadata.actor_type.value,
        actor_id=metadata.actor_id,
        occurred_at=metadata.occurred_at,
        payload=payload,
    )

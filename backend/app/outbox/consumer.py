"""Idempotent projection of closed domain events into sanitized DOMAIN traces."""

from __future__ import annotations

import json

from app.application.events import DomainEventType
from app.infrastructure.database.models.observability import TraceSource
from app.outbox.models import ClaimedOutboxEvent
from app.trace.models import TracePayload
from app.trace.runtime import TraceRuntime


class UnsupportedOutboxEvent(ValueError):
    pass


class TraceDomainEventProjector:
    _TRACE_EVENT_TYPES = {
        DomainEventType.TICKET_CREATED.value: "domain_ticket_created",
        DomainEventType.TICKET_STATUS_CHANGED.value: "domain_ticket_status_changed",
        DomainEventType.APPOINTMENT_BOOKED.value: "domain_appointment_booked",
        DomainEventType.APPOINTMENT_RESCHEDULED.value: "domain_appointment_rescheduled",
        DomainEventType.TICKET_ESCALATED.value: "domain_ticket_escalated",
    }

    def __init__(self, trace: TraceRuntime) -> None:
        self._trace = trace

    async def consume(self, event: ClaimedOutboxEvent) -> None:
        trace_event_type = self._TRACE_EVENT_TYPES.get(event.event_type)
        if trace_event_type is None:
            raise UnsupportedOutboxEvent(f"unsupported event type: {event.event_type}")
        payload_data = dict(event.payload)
        payload_data.update(
            aggregate_type=event.aggregate_type,
            aggregate_id=str(event.aggregate_id),
            aggregate_version=event.aggregate_version,
            operation_id=str(event.operation_id),
        )
        payload = TracePayload.model_validate_json(json.dumps(payload_data))
        await self._trace.append_event(
            event_key=f"outbox:{event.event_key}",
            run_id=event.run_id,
            thread_id=event.thread_id,
            trace_id=event.trace_id,
            source=TraceSource.DOMAIN,
            event_type=trace_event_type,
            operation_id=event.operation_id,
            payload=payload,
            occurred_at=event.occurred_at,
        )

"""Transactional outbox writer used by deterministic application services."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.application.events import DomainEvent
from app.infrastructure.database.models.observability import OutboxEvent, OutboxStatus


class SqlAlchemyOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, event: DomainEvent) -> None:
        self._session.add(
            OutboxEvent(
                id=event.event_id,
                event_key=event.event_key,
                event_type=event.event_type.value,
                aggregate_type=event.aggregate_type.value,
                aggregate_id=event.aggregate_id,
                aggregate_version=event.aggregate_version,
                operation_id=event.operation_id,
                trace_id=event.trace_id,
                run_id=event.run_id,
                thread_id=event.thread_id,
                actor_type=event.actor_type,
                actor_id=event.actor_id,
                payload=dict(event.payload),
                status=OutboxStatus.PENDING,
                occurred_at=event.occurred_at,
            )
        )

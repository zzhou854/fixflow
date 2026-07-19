"""SQLAlchemy append-only Worker Event persistence."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import WorkerEventRecord
from app.domain.enums import WorkerEventType
from app.domain.models import WorkerEventSnapshot
from app.infrastructure.database.mappers import worker_event_to_snapshot
from app.infrastructure.database.models.event import WorkerEvent


class SqlAlchemyWorkerEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for_appointment(self, appointment_id: UUID) -> list[WorkerEventSnapshot]:
        rows = await self._session.scalars(
            select(WorkerEvent)
            .where(WorkerEvent.appointment_id == appointment_id)
            .order_by(WorkerEvent.sequence_no)
        )
        return [worker_event_to_snapshot(row) for row in rows]

    async def get_by_external_key(self, key: str) -> WorkerEventSnapshot | None:
        row = await self._session.scalar(
            select(WorkerEvent).where(WorkerEvent.external_event_key == key)
        )
        return worker_event_to_snapshot(row) if row is not None else None

    async def add(self, record: WorkerEventRecord) -> None:
        self._session.add(
            WorkerEvent(
                id=record.event_id,
                appointment_id=record.appointment_id,
                subject_worker_id=record.subject_worker_id,
                sequence_no=record.sequence_no,
                event_type=WorkerEventType(record.event_type),
                actor_type=record.actor_type,
                actor_id=str(record.actor_id),
                external_event_key=record.external_event_key,
                request_hash=record.request_hash,
                trace_id=record.trace_id,
                occurred_at=record.occurred_at,
                payload=record.payload,
            )
        )

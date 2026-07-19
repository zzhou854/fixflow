"""SQLAlchemy appointment, eligibility, and appointment-history persistence."""

from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql.ranges import Range
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import PersistenceConflict
from app.application.ports import AppointmentHistoryRecord
from app.domain.enums import AppointmentStatus, WorkerSkillType
from app.domain.models import AppointmentDraft, AppointmentSnapshot
from app.infrastructure.database.mappers import appointment_to_snapshot
from app.infrastructure.database.models.appointment import Appointment, AppointmentStatusHistory
from app.infrastructure.database.models.worker import Worker, WorkerAvailability, WorkerSkill


class SqlAlchemyAppointmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(
        self, appointment_id: UUID, *, for_update: bool = False
    ) -> AppointmentSnapshot | None:
        statement = select(Appointment).where(Appointment.id == appointment_id)
        if for_update:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        return appointment_to_snapshot(row) if row is not None else None

    async def latest_for_ticket(self, ticket_id: UUID) -> AppointmentSnapshot | None:
        row = await self._session.scalar(
            select(Appointment)
            .where(Appointment.ticket_id == ticket_id)
            .order_by(Appointment.created_at.desc(), Appointment.id.desc())
            .limit(1)
        )
        return appointment_to_snapshot(row) if row is not None else None

    async def active_for_ticket(self, ticket_id: UUID) -> AppointmentSnapshot | None:
        row = await self._session.scalar(
            select(Appointment).where(
                Appointment.ticket_id == ticket_id,
                Appointment.status == AppointmentStatus.BOOKED,
            )
        )
        return appointment_to_snapshot(row) if row is not None else None

    async def worker_can_service(
        self,
        worker_id: UUID,
        skill: WorkerSkillType,
        starts_at: datetime,
        ends_at: datetime,
    ) -> bool:
        requested = Range(starts_at, ends_at, bounds="[)")
        statement = select(
            select(Worker.id)
            .join(WorkerSkill, WorkerSkill.worker_id == Worker.id)
            .join(WorkerAvailability, WorkerAvailability.worker_id == Worker.id)
            .where(
                Worker.id == worker_id,
                Worker.is_active.is_(True),
                WorkerSkill.skill_type == skill,
                WorkerAvailability.available_range.contains(requested),
            )
            .exists()
        )
        return bool(await self._session.scalar(statement))

    async def add(self, draft: AppointmentDraft) -> None:
        self._session.add(
            Appointment(
                id=draft.appointment_id,
                ticket_id=draft.ticket_id,
                worker_id=draft.worker_id,
                purpose=draft.purpose,
                status=draft.status,
                scheduled_range=Range(draft.starts_at, draft.ends_at, bounds="[)"),
                supersedes_appointment_id=draft.supersedes_appointment_id,
                version=1,
            )
        )

    async def update(
        self,
        snapshot: AppointmentSnapshot,
        *,
        expected_version: int,
        outcome: dict[str, Any] | None = None,
    ) -> None:
        if snapshot.version != expected_version + 1:
            raise PersistenceConflict("invalid_version_step")
        values: dict[str, Any] = {
            "status": snapshot.status,
            "version": Appointment.version + 1,
            "updated_at": func.now(),
        }
        if outcome is not None:
            values.update(outcome)
        result = await self._session.execute(
            update(Appointment)
            .where(
                Appointment.id == snapshot.appointment_id, Appointment.version == expected_version
            )
            .values(**values)
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            raise PersistenceConflict(
                "version_conflict",
                resource_type="appointment",
                resource_id=snapshot.appointment_id,
            )

    async def add_history(self, record: AppointmentHistoryRecord) -> None:
        self._session.add(
            AppointmentStatusHistory(
                appointment_id=record.appointment_id,
                from_status=record.from_status,
                to_status=record.to_status,
                actor_type=record.actor_type,
                actor_id=str(record.actor_id),
                reason_code=record.reason_code,
                reason_text=record.reason_text,
                evidence={"items": list(record.evidence)},
                trace_id=record.trace_id,
                occurred_at=record.occurred_at,
                version_before=record.version_before,
                version_after=record.version_after,
            )
        )

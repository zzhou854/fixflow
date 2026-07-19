"""SQLAlchemy implementation of the focused deterministic read-query port."""

from collections import defaultdict
from datetime import datetime
from typing import cast
from uuid import UUID

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.query_models import ResidentPropertyReadModel, SlotWorkerSource, TimeWindow
from app.domain.enums import AppointmentStatus, TicketStatus, WorkerSkillType
from app.infrastructure.database.models import (
    Appointment,
    Property,
    RepairTicket,
    ResidentPropertyRelation,
    Worker,
    WorkerAvailability,
    WorkerSkill,
)


class SqlAlchemyQueryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_property(
        self, resident_id: UUID, property_id: UUID
    ) -> ResidentPropertyReadModel | None:
        row = (
            await self._session.execute(
                select(Property)
                .join(
                    ResidentPropertyRelation,
                    ResidentPropertyRelation.property_id == Property.id,
                )
                .where(
                    Property.id == property_id,
                    Property.is_active.is_(True),
                    ResidentPropertyRelation.resident_id == resident_id,
                    ResidentPropertyRelation.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        return ResidentPropertyReadModel(
            resident_id=resident_id,
            property_id=row.id,
            community_name=row.community_name,
            building_no=row.building_no,
            unit_no=row.unit_no,
            room_no=row.room_no,
            address_text=row.address_text,
        )

    async def get_property_service_area(self, property_id: UUID) -> str | None:
        return cast(
            str | None,
            await self._session.scalar(
                select(Property.community_name).where(
                    Property.id == property_id, Property.is_active.is_(True)
                )
            ),
        )

    async def list_slot_worker_sources(
        self,
        *,
        skill: WorkerSkillType,
        search_window_start: datetime,
        search_window_end: datetime,
    ) -> list[SlotWorkerSource]:
        workers = list(
            await self._session.scalars(
                select(Worker)
                .join(WorkerSkill, WorkerSkill.worker_id == Worker.id)
                .where(Worker.is_active.is_(True), WorkerSkill.skill_type == skill)
                .order_by(Worker.id)
            )
        )
        if not workers:
            return []
        worker_ids = [worker.id for worker in workers]
        availability_rows = (
            await self._session.execute(
                select(WorkerAvailability.worker_id, WorkerAvailability.available_range).where(
                    WorkerAvailability.worker_id.in_(worker_ids),
                    WorkerAvailability.available_range.op("&&")(
                        func.tstzrange(search_window_start, search_window_end, "[)")
                    ),
                )
            )
        ).all()
        booking_rows = (
            await self._session.execute(
                select(Appointment.worker_id, Appointment.scheduled_range).where(
                    Appointment.worker_id.in_(worker_ids),
                    Appointment.status == AppointmentStatus.BOOKED,
                    Appointment.scheduled_range.op("&&")(
                        func.tstzrange(search_window_start, search_window_end, "[)")
                    ),
                )
            )
        ).all()
        terminal = (TicketStatus.CANCELLED, TicketStatus.CLOSED)
        workload_rows = (
            await self._session.execute(
                select(Appointment.worker_id, func.count(distinct(Appointment.ticket_id)))
                .join(RepairTicket, RepairTicket.id == Appointment.ticket_id)
                .where(
                    Appointment.worker_id.in_(worker_ids),
                    Appointment.status == AppointmentStatus.BOOKED,
                    RepairTicket.status.not_in(terminal),
                )
                .group_by(Appointment.worker_id)
            )
        ).all()
        availability: dict[UUID, list[TimeWindow]] = defaultdict(list)
        for worker_id, interval in availability_rows:
            if interval.lower is not None and interval.upper is not None:
                availability[worker_id].append(
                    TimeWindow(
                        starts_at=interval.lower,
                        ends_at=interval.upper,
                        lower_inclusive=interval.bounds.startswith("["),
                    )
                )
        bookings: dict[UUID, list[TimeWindow]] = defaultdict(list)
        for worker_id, interval in booking_rows:
            if interval.lower is not None and interval.upper is not None:
                bookings[worker_id].append(
                    TimeWindow(starts_at=interval.lower, ends_at=interval.upper)
                )
        workloads = {worker_id: int(count) for worker_id, count in workload_rows}
        return [
            SlotWorkerSource(
                worker_id=worker.id,
                worker_name=worker.name,
                skill_type=skill,
                service_area=worker.service_area,
                open_ticket_count=workloads.get(worker.id, 0),
                availability=tuple(availability[worker.id]),
                booked_intervals=tuple(bookings[worker.id]),
            )
            for worker in workers
        ]

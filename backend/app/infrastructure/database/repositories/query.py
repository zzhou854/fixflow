"""SQLAlchemy implementation of the focused deterministic read-query port."""

from collections import defaultdict
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.query_models import (
    AppointmentHistoryReadModel,
    AppointmentReadModel,
    ResidentPropertyReadModel,
    SlotWorkerSource,
    TicketDetailReadModel,
    TicketHistoryReadModel,
    TicketListItemReadModel,
    TimeWindow,
    WorkerEventReadModel,
)
from app.domain.enums import (
    AppointmentStatus,
    IssueCategory,
    Severity,
    TicketStatus,
    WorkerSkillType,
)
from app.infrastructure.database.models import (
    Appointment,
    AppointmentStatusHistory,
    Property,
    RepairTicket,
    ResidentPropertyRelation,
    TicketStatusHistory,
    User,
    Worker,
    WorkerAvailability,
    WorkerEvent,
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

    async def list_resident_properties(self, resident_id: UUID) -> list[ResidentPropertyReadModel]:
        rows = list(
            await self._session.scalars(
                select(Property)
                .join(ResidentPropertyRelation)
                .where(
                    ResidentPropertyRelation.resident_id == resident_id,
                    ResidentPropertyRelation.is_active.is_(True),
                    Property.is_active.is_(True),
                )
                .order_by(Property.community_name, Property.building_no, Property.room_no)
            )
        )
        return [
            ResidentPropertyReadModel(
                resident_id=resident_id,
                property_id=row.id,
                community_name=row.community_name,
                building_no=row.building_no,
                unit_no=row.unit_no,
                room_no=row.room_no,
                address_text=row.address_text,
            )
            for row in rows
        ]

    async def list_resident_tickets(
        self, resident_id: UUID, *, limit: int, offset: int
    ) -> list[TicketListItemReadModel]:
        return await self._list_tickets(
            RepairTicket.resident_id == resident_id,
            limit=limit,
            offset=offset,
        )

    async def list_operator_tickets(
        self,
        *,
        ticket_status: TicketStatus | None,
        issue_category: IssueCategory | None,
        severity: Severity | None,
        limit: int,
        offset: int,
    ) -> list[TicketListItemReadModel]:
        filters: list[Any] = []
        if ticket_status is not None:
            filters.append(RepairTicket.status == ticket_status)
        if issue_category is not None:
            filters.append(RepairTicket.issue_category == issue_category)
        if severity is not None:
            filters.append(RepairTicket.severity == severity)
        return await self._list_tickets(*filters, limit=limit, offset=offset)

    async def _list_tickets(
        self, *filters: Any, limit: int, offset: int
    ) -> list[TicketListItemReadModel]:
        rows = (
            await self._session.execute(
                select(RepairTicket, User, Property, Appointment)
                .join(User, User.id == RepairTicket.resident_id)
                .join(Property, Property.id == RepairTicket.property_id)
                .outerjoin(
                    Appointment,
                    (Appointment.ticket_id == RepairTicket.id)
                    & (Appointment.status == AppointmentStatus.BOOKED),
                )
                .where(*filters)
                .order_by(RepairTicket.updated_at.desc(), RepairTicket.id)
                .limit(limit)
                .offset(offset)
            )
        ).all()
        return [
            self._ticket_item(ticket, user, property_, appointment)
            for ticket, user, property_, appointment in rows
        ]

    async def get_ticket_detail(self, ticket_id: UUID) -> TicketDetailReadModel | None:
        base = (
            await self._session.execute(
                select(RepairTicket, User, Property, Appointment)
                .join(User, User.id == RepairTicket.resident_id)
                .join(Property, Property.id == RepairTicket.property_id)
                .outerjoin(
                    Appointment,
                    (Appointment.ticket_id == RepairTicket.id)
                    & (Appointment.status == AppointmentStatus.BOOKED),
                )
                .where(RepairTicket.id == ticket_id)
            )
        ).one_or_none()
        if base is None:
            return None
        ticket, user, property_, active = base
        latest = active or await self._session.scalar(
            select(Appointment)
            .where(Appointment.ticket_id == ticket_id)
            .order_by(Appointment.created_at.desc())
            .limit(1)
        )
        ticket_history = list(
            await self._session.scalars(
                select(TicketStatusHistory)
                .where(TicketStatusHistory.ticket_id == ticket_id)
                .order_by(TicketStatusHistory.version_after)
            )
        )
        appointment_history = (
            list(
                await self._session.scalars(
                    select(AppointmentStatusHistory)
                    .where(AppointmentStatusHistory.appointment_id == latest.id)
                    .order_by(AppointmentStatusHistory.version_after)
                )
            )
            if latest is not None
            else []
        )
        event = (
            await self._session.scalar(
                select(WorkerEvent)
                .where(WorkerEvent.appointment_id == latest.id)
                .order_by(WorkerEvent.sequence_no.desc())
                .limit(1)
            )
            if latest is not None
            else None
        )
        return TicketDetailReadModel(
            ticket=self._ticket_item(ticket, user, property_, active),
            issue_description=ticket.issue_description,
            escalated_from_status=ticket.escalated_from_status,
            ticket_history=tuple(
                TicketHistoryReadModel(
                    from_status=row.from_status,
                    to_status=row.to_status,
                    action=row.action,
                    actor_type=row.actor_type,
                    reason_code=row.reason_code,
                    reason_text=row.reason_text,
                    version_after=row.version_after,
                    created_at=row.created_at,
                )
                for row in ticket_history
            ),
            appointment_history=tuple(
                AppointmentHistoryReadModel(
                    from_status=row.from_status,
                    to_status=row.to_status,
                    actor_type=row.actor_type,
                    reason_code=row.reason_code,
                    reason_text=row.reason_text,
                    version_after=row.version_after,
                    created_at=row.created_at,
                )
                for row in appointment_history
            ),
            latest_worker_event=(
                WorkerEventReadModel(
                    event_id=event.id,
                    event_type=event.event_type,
                    sequence_no=event.sequence_no,
                    subject_worker_id=event.subject_worker_id,
                )
                if event is not None
                else None
            ),
        )

    @classmethod
    def _ticket_item(
        cls, ticket: RepairTicket, user: User, property_: Property, appointment: Appointment | None
    ) -> TicketListItemReadModel:
        return TicketListItemReadModel(
            ticket_id=ticket.id,
            resident_id=ticket.resident_id,
            resident_username=user.username,
            property_id=ticket.property_id,
            property_label=property_.address_text,
            issue_category=ticket.issue_category,
            issue_location=ticket.issue_location,
            severity=ticket.severity,
            ticket_status=ticket.status,
            rework_count=ticket.rework_count,
            version=ticket.version,
            appointment=cls._appointment(appointment),
            updated_at=ticket.updated_at,
        )

    @staticmethod
    def _appointment(row: Appointment | None) -> AppointmentReadModel | None:
        if row is None or row.scheduled_range.lower is None or row.scheduled_range.upper is None:
            return None
        return AppointmentReadModel(
            appointment_id=row.id,
            worker_id=row.worker_id,
            purpose=row.purpose,
            status=row.status,
            scheduled_start=row.scheduled_range.lower,
            scheduled_end=row.scheduled_range.upper,
            appointment_version=row.version,
        )

    async def list_slot_worker_sources(
        self,
        *,
        skill: WorkerSkillType,
        search_window_start: datetime,
        search_window_end: datetime,
        excluded_appointment_id: UUID | None = None,
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
        booking_query = select(Appointment.worker_id, Appointment.scheduled_range).where(
            Appointment.worker_id.in_(worker_ids),
            Appointment.status == AppointmentStatus.BOOKED,
            Appointment.scheduled_range.op("&&")(
                func.tstzrange(search_window_start, search_window_end, "[)")
            ),
        )
        if excluded_appointment_id is not None:
            booking_query = booking_query.where(Appointment.id != excluded_appointment_id)
        booking_rows = (
            await self._session.execute(
                booking_query
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

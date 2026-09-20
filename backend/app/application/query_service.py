"""Authorized, deterministic read use cases backed by current PostgreSQL snapshots."""

from uuid import UUID

from app.application.errors import AuthorizationFailed, ResourceNotFound
from app.application.ports import UnitOfWork, UnitOfWorkFactory
from app.application.query_models import (
    AppointmentReadModel,
    AvailableSlotReadModel,
    FindOpenRepairTicketsQuery,
    GetResidentPropertyQuery,
    GetTicketSnapshotQuery,
    ListAvailableSlotsQuery,
    OpenRepairTicketReadModel,
    QueryActor,
    ResidentPropertyReadModel,
    TicketDetailReadModel,
    TicketListItemReadModel,
    TicketSnapshotReadModel,
    WorkerEventReadModel,
)
from app.application.slot_queries import generate_available_slots, validate_slot_query
from app.domain.enums import (
    ISSUE_CATEGORY_REQUIRED_SKILL,
    ActorType,
    IssueCategory,
    Severity,
    TicketStatus,
)
from app.domain.models import TicketSnapshot


class FixFlowQueryService:
    """Small query service; it does not own mutations or a generic query framework."""

    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    @staticmethod
    async def _authorize_property(
        uow: UnitOfWork, actor: QueryActor, resident_id: UUID, property_id: UUID
    ) -> None:
        if actor.actor_type is ActorType.RESIDENT:
            if actor.actor_id != resident_id or not await uow.tickets.resident_has_property(
                actor.actor_id,
                property_id,
            ):
                raise AuthorizationFailed("resident_not_authorized")
            return
        if actor.actor_type is ActorType.OPERATOR and await uow.tickets.actor_is_operator(
            actor.actor_id
        ):
            return
        raise AuthorizationFailed("actor_not_authorized")

    @staticmethod
    async def _authorize_ticket(uow: UnitOfWork, actor: QueryActor, ticket: TicketSnapshot) -> None:
        await FixFlowQueryService._authorize_property(
            uow, actor, ticket.resident_id, ticket.property_id
        )

    async def get_resident_property(
        self, query: GetResidentPropertyQuery
    ) -> ResidentPropertyReadModel:
        async with self._uow_factory() as uow:
            await self._authorize_property(uow, query.actor, query.resident_id, query.property_id)
            result = await uow.queries.get_property(query.resident_id, query.property_id)
            if result is None:
                raise ResourceNotFound("property_relation_not_found")
            return result

    async def find_open_repair_tickets(
        self, query: FindOpenRepairTicketsQuery
    ) -> tuple[OpenRepairTicketReadModel, ...]:
        async with self._uow_factory() as uow:
            await self._authorize_property(uow, query.actor, query.resident_id, query.property_id)
            tickets = await uow.tickets.find_exact_open_matches(
                query.resident_id,
                query.property_id,
                query.issue_category,
                query.normalized_issue_location,
            )
            return tuple(self._open_ticket(item) for item in tickets)

    async def get_ticket_snapshot(self, query: GetTicketSnapshotQuery) -> TicketSnapshotReadModel:
        async with self._uow_factory() as uow:
            ticket = await uow.tickets.get(query.ticket_id)
            if ticket is None:
                raise ResourceNotFound("ticket_not_found")
            await self._authorize_ticket(uow, query.actor, ticket)
            active = await uow.appointments.active_for_ticket(ticket.ticket_id)
            latest = active or await uow.appointments.latest_for_ticket(ticket.ticket_id)
            events = (
                await uow.worker_events.list_for_appointment(latest.appointment_id)
                if latest is not None
                else ()
            )
            return TicketSnapshotReadModel(
                ticket_id=ticket.ticket_id,
                ticket_version=ticket.version,
                resident_id=ticket.resident_id,
                property_id=ticket.property_id,
                issue_category=ticket.issue_category,
                issue_location=ticket.issue_location,
                severity=ticket.severity,
                ticket_status=ticket.status,
                rework_count=ticket.rework_count,
                escalated_from_status=ticket.escalated_from_status,
                active_appointment=(
                    AppointmentReadModel(
                        appointment_id=active.appointment_id,
                        worker_id=active.worker_id,
                        purpose=active.purpose,
                        status=active.status,
                        scheduled_start=active.starts_at,
                        scheduled_end=active.ends_at,
                        appointment_version=active.version,
                    )
                    if active is not None
                    else None
                ),
                latest_worker_event=(
                    WorkerEventReadModel(
                        event_id=events[-1].event_id,
                        event_type=events[-1].event_type,
                        sequence_no=events[-1].sequence_no,
                        subject_worker_id=events[-1].subject_worker_id,
                    )
                    if events
                    else None
                ),
            )

    async def list_available_slots(
        self, query: ListAvailableSlotsQuery
    ) -> tuple[AvailableSlotReadModel, ...]:
        validate_slot_query(
            query.search_window_start,
            query.search_window_end,
            query.requested_duration_minutes,
            query.max_results,
        )
        async with self._uow_factory() as uow:
            if query.actor.actor_type is ActorType.RESIDENT:
                if not await uow.tickets.resident_has_property(
                    query.actor.actor_id, query.property_id
                ):
                    raise AuthorizationFailed("resident_not_authorized")
            elif query.actor.actor_type is ActorType.OPERATOR:
                if not await uow.tickets.actor_is_operator(query.actor.actor_id):
                    raise AuthorizationFailed("operator_not_authorized")
            else:
                raise AuthorizationFailed("actor_not_authorized")
            service_area = await uow.queries.get_property_service_area(query.property_id)
            if service_area is None:
                raise ResourceNotFound("property_not_found")
            sources = await uow.queries.list_slot_worker_sources(
                skill=ISSUE_CATEGORY_REQUIRED_SKILL[query.issue_category],
                search_window_start=query.search_window_start,
                search_window_end=query.search_window_end,
                excluded_appointment_id=query.excluded_appointment_id,
            )
            return generate_available_slots(
                sources=tuple(sources),
                target_service_area=service_area,
                search_window_start=query.search_window_start,
                search_window_end=query.search_window_end,
                requested_duration_minutes=query.requested_duration_minutes,
                max_results=query.max_results,
            )

    async def list_resident_properties(
        self, actor: QueryActor
    ) -> tuple[ResidentPropertyReadModel, ...]:
        if actor.actor_type is not ActorType.RESIDENT or actor.actor_id is None:
            raise AuthorizationFailed("resident_required")
        async with self._uow_factory() as uow:
            return tuple(await uow.queries.list_resident_properties(actor.actor_id))

    async def list_resident_tickets(
        self, actor: QueryActor, *, limit: int, offset: int
    ) -> tuple[TicketListItemReadModel, ...]:
        if actor.actor_type is not ActorType.RESIDENT:
            raise AuthorizationFailed("resident_required")
        async with self._uow_factory() as uow:
            return tuple(
                await uow.queries.list_resident_tickets(actor.actor_id, limit=limit, offset=offset)
            )

    async def list_operator_tickets(
        self,
        actor: QueryActor,
        *,
        ticket_status: TicketStatus | None,
        issue_category: IssueCategory | None,
        severity: Severity | None,
        limit: int,
        offset: int,
    ) -> tuple[TicketListItemReadModel, ...]:
        async with self._uow_factory() as uow:
            if (
                actor.actor_type is not ActorType.OPERATOR
                or not await uow.tickets.actor_is_operator(actor.actor_id)
            ):
                raise AuthorizationFailed("operator_required")
            return tuple(
                await uow.queries.list_operator_tickets(
                    ticket_status=ticket_status,
                    issue_category=issue_category,
                    severity=severity,
                    limit=limit,
                    offset=offset,
                )
            )

    async def get_ticket_detail(self, actor: QueryActor, ticket_id: UUID) -> TicketDetailReadModel:
        async with self._uow_factory() as uow:
            ticket = await uow.tickets.get(ticket_id)
            if ticket is None:
                raise ResourceNotFound("ticket_not_found")
            await self._authorize_ticket(uow, actor, ticket)
            detail = await uow.queries.get_ticket_detail(ticket_id)
            if detail is None:
                raise ResourceNotFound("ticket_not_found")
            return detail

    @staticmethod
    def _open_ticket(ticket: TicketSnapshot) -> OpenRepairTicketReadModel:
        return OpenRepairTicketReadModel(
            ticket_id=ticket.ticket_id,
            ticket_version=ticket.version,
            resident_id=ticket.resident_id,
            property_id=ticket.property_id,
            issue_category=ticket.issue_category,
            issue_location=ticket.issue_location,
            severity=ticket.severity,
            ticket_status=ticket.status,
            rework_count=ticket.rework_count,
        )

"""Formal booking and atomic rescheduling use cases."""

from dataclasses import asdict, replace

from app.application.errors import ApplicationError, AuthorizationFailed, ResourceNotFound
from app.application.events import AggregateType, DomainEventType, build_domain_event
from app.application.models import (
    BookAppointmentCommand,
    OperationResult,
    RescheduleAppointmentCommand,
)
from app.application.ports import AppointmentHistoryRecord, UnitOfWork
from app.application.service_support import TransactionalService
from app.domain.appointments import plan_appointment_creation, plan_reschedule
from app.domain.enums import (
    ISSUE_CATEGORY_REQUIRED_SKILL,
    AppointmentPurpose,
    AppointmentStatus,
    TicketAction,
    TicketStatus,
)
from app.domain.invariants import plan_rework_appointment
from app.domain.models import AppointmentDraft
from app.domain.tickets import TicketTransitionRequest, transition_ticket


class AppointmentApplicationService(TransactionalService):
    async def book_appointment(self, command: BookAppointmentCommand) -> OperationResult:
        async def handler(uow: UnitOfWork, _digest: str) -> OperationResult:
            return await self._book_appointment(uow, command)

        return await self._execute(
            scope="book_appointment",
            metadata=command.metadata,
            payload=asdict(command),
            handler=handler,
        )

    async def _book_appointment(
        self, uow: UnitOfWork, command: BookAppointmentCommand
    ) -> OperationResult:
        if command.starts_at < command.metadata.occurred_at:
            raise ApplicationError("appointment_time_in_past")
        ticket = await uow.tickets.get(command.ticket_id)
        if ticket is None:
            raise ResourceNotFound("ticket_not_found", ticket_id=command.ticket_id)
        await self._authorize_ticket(uow, ticket, command.metadata)
        skill = ISSUE_CATEGORY_REQUIRED_SKILL[ticket.issue_category]
        if not await uow.appointments.worker_can_service(
            command.worker_id,
            ticket.property_id,
            skill,
            command.starts_at,
            command.ends_at,
        ):
            raise AuthorizationFailed("worker_not_eligible", worker_id=command.worker_id)
        purpose = (
            AppointmentPurpose.REWORK
            if ticket.status is TicketStatus.REWORK_REQUIRED
            else AppointmentPurpose.INITIAL_REPAIR
        )
        appointment_id = self._id_factory()
        draft = AppointmentDraft(
            appointment_id=appointment_id,
            ticket_id=ticket.ticket_id,
            worker_id=command.worker_id,
            purpose=purpose,
            starts_at=command.starts_at,
            ends_at=command.ends_at,
        )
        plan = plan_appointment_creation(
            draft,
            current_ticket_status=ticket.status,
            actor_type=command.metadata.actor_type,
            expected_ticket_version=command.expected_ticket_version,
            actual_ticket_version=ticket.version,
        )
        if purpose is AppointmentPurpose.REWORK:
            prior = await uow.appointments.latest_for_ticket(ticket.ticket_id)
            if prior is None:
                raise ResourceNotFound("prior_repair_appointment_not_found")
            plan_rework_appointment(
                ticket_id=ticket.ticket_id,
                current_status=ticket.status,
                current_rework_count=ticket.rework_count,
                prior_appointment=prior,
                replacement=draft,
            )
        updated = replace(
            ticket,
            status=plan.next_ticket_status,
            version=plan.next_ticket_version,
            escalated_from_status=None,
        )
        await uow.tickets.update(updated, expected_version=ticket.version)
        await uow.appointments.add(draft)
        await uow.tickets.add_history(
            self._ticket_history(ticket, updated, "BOOK_APPOINTMENT", command.metadata)
        )
        await uow.appointments.add_history(
            AppointmentHistoryRecord(
                appointment_id=appointment_id,
                from_status=None,
                to_status=AppointmentStatus.BOOKED,
                actor_type=command.metadata.actor_type,
                actor_id=command.metadata.actor_id,
                trace_id=command.metadata.trace_id,
                version_before=0,
                version_after=1,
                occurred_at=command.metadata.occurred_at,
            )
        )
        await self._emit_ticket_status_changed(
            uow,
            ticket,
            updated,
            "BOOK_APPOINTMENT",
            command.metadata,
            scope="book_appointment",
        )
        await uow.outbox.add(
            build_domain_event(
                event_type=DomainEventType.APPOINTMENT_BOOKED,
                aggregate_type=AggregateType.APPOINTMENT,
                aggregate_id=appointment_id,
                aggregate_version=1,
                metadata=command.metadata,
                scope="book_appointment",
                payload={
                    "ticket_id": str(ticket.ticket_id),
                    "worker_id": str(command.worker_id),
                    "purpose": purpose.value,
                    "starts_at": command.starts_at.isoformat(),
                    "ends_at": command.ends_at.isoformat(),
                },
            )
        )
        return OperationResult(
            ok=True,
            code="APPOINTMENT_BOOKED",
            resource_type="appointment",
            resource_id=appointment_id,
            resource_version=1,
            data={"ticket_id": str(ticket.ticket_id), "ticket_version": updated.version},
        )

    async def reschedule_appointment(
        self, command: RescheduleAppointmentCommand
    ) -> OperationResult:
        async def handler(uow: UnitOfWork, _digest: str) -> OperationResult:
            return await self._reschedule_appointment(uow, command)

        return await self._execute(
            scope="reschedule_appointment",
            metadata=command.metadata,
            payload=asdict(command),
            handler=handler,
        )

    async def _reschedule_appointment(
        self, uow: UnitOfWork, command: RescheduleAppointmentCommand
    ) -> OperationResult:
        if command.starts_at < command.metadata.occurred_at:
            raise ApplicationError("appointment_time_in_past")
        ticket = await uow.tickets.get(command.ticket_id)
        old = await uow.appointments.get(command.appointment_id)
        if ticket is None:
            raise ResourceNotFound("ticket_not_found")
        if old is None or old.ticket_id != ticket.ticket_id:
            raise ResourceNotFound("appointment_not_found")
        await self._authorize_ticket(uow, ticket, command.metadata)
        skill = ISSUE_CATEGORY_REQUIRED_SKILL[ticket.issue_category]
        if not await uow.appointments.worker_can_service(
            command.worker_id,
            ticket.property_id,
            skill,
            command.starts_at,
            command.ends_at,
        ):
            raise AuthorizationFailed("worker_not_eligible", worker_id=command.worker_id)
        replacement_id = self._id_factory()
        draft = AppointmentDraft(
            appointment_id=replacement_id,
            ticket_id=ticket.ticket_id,
            worker_id=command.worker_id,
            purpose=old.purpose,
            starts_at=command.starts_at,
            ends_at=command.ends_at,
            supersedes_appointment_id=old.appointment_id,
        )
        plan = plan_reschedule(
            old,
            draft,
            actor_type=command.metadata.actor_type,
            expected_version=command.expected_appointment_version,
        )
        ticket_plan = transition_ticket(
            TicketTransitionRequest(
                current_status=ticket.status,
                action=TicketAction.RESCHEDULE,
                actor_type=command.metadata.actor_type,
                expected_version=command.expected_ticket_version,
                actual_version=ticket.version,
                resident_authorized=True,
                has_active_booked_appointment=True,
                current_rework_count=ticket.rework_count,
            )
        )
        updated_ticket = replace(ticket, version=ticket_plan.next_version)
        updated_old = replace(
            old,
            status=plan.old_transition.next_status,
            version=plan.old_transition.next_version,
        )
        await uow.tickets.update(updated_ticket, expected_version=ticket.version)
        await uow.appointments.update(updated_old, expected_version=old.version)
        await uow.appointments.add(draft)
        await uow.tickets.add_history(
            self._ticket_history(ticket, updated_ticket, "RESCHEDULE", command.metadata)
        )
        await uow.appointments.add_history(
            self._appointment_history(old, updated_old, command.metadata)
        )
        await uow.appointments.add_history(
            AppointmentHistoryRecord(
                appointment_id=replacement_id,
                from_status=None,
                to_status=AppointmentStatus.BOOKED,
                actor_type=command.metadata.actor_type,
                actor_id=command.metadata.actor_id,
                trace_id=command.metadata.trace_id,
                version_before=0,
                version_after=1,
                occurred_at=command.metadata.occurred_at,
            )
        )
        await uow.outbox.add(
            build_domain_event(
                event_type=DomainEventType.APPOINTMENT_RESCHEDULED,
                aggregate_type=AggregateType.APPOINTMENT,
                aggregate_id=replacement_id,
                aggregate_version=1,
                metadata=command.metadata,
                scope="reschedule_appointment",
                payload={
                    "ticket_id": str(ticket.ticket_id),
                    "previous_appointment_id": str(old.appointment_id),
                    "worker_id": str(command.worker_id),
                    "starts_at": command.starts_at.isoformat(),
                    "ends_at": command.ends_at.isoformat(),
                },
            )
        )
        return OperationResult(
            ok=True,
            code="APPOINTMENT_RESCHEDULED",
            resource_type="appointment",
            resource_id=replacement_id,
            resource_version=1,
            data={
                "supersedes_appointment_id": str(old.appointment_id),
                "ticket_version": updated_ticket.version,
            },
        )

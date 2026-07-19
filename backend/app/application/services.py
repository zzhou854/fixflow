"""Small facade preserving one deterministic boundary for future adapters."""

from collections.abc import Callable
from uuid import UUID, uuid4

from app.application.appointment_service import AppointmentApplicationService
from app.application.models import (
    BookAppointmentCommand,
    CreateTicketCommand,
    EscalateTicketCommand,
    OperationResult,
    RecordWorkerEventCommand,
    RecoverTicketCommand,
    RescheduleAppointmentCommand,
    ReviewRepairCommand,
)
from app.application.ports import UnitOfWorkFactory
from app.application.ticket_service import TicketApplicationService
from app.application.worker_event_service import WorkerEventApplicationService


class FixFlowApplicationService:
    """Facade over focused ticket, appointment, and Worker Event services."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._tickets = TicketApplicationService(uow_factory, id_factory=id_factory)
        self._appointments = AppointmentApplicationService(uow_factory, id_factory=id_factory)
        self._worker_events = WorkerEventApplicationService(uow_factory, id_factory=id_factory)

    async def create_ticket(self, command: CreateTicketCommand) -> OperationResult:
        return await self._tickets.create_ticket(command)

    async def book_appointment(self, command: BookAppointmentCommand) -> OperationResult:
        return await self._appointments.book_appointment(command)

    async def reschedule_appointment(
        self, command: RescheduleAppointmentCommand
    ) -> OperationResult:
        return await self._appointments.reschedule_appointment(command)

    async def record_worker_event(self, command: RecordWorkerEventCommand) -> OperationResult:
        return await self._worker_events.record_worker_event(command)

    async def review_repair(self, command: ReviewRepairCommand) -> OperationResult:
        return await self._tickets.review_repair(command)

    async def escalate_ticket(self, command: EscalateTicketCommand) -> OperationResult:
        return await self._tickets.escalate_ticket(command)

    async def recover_ticket(self, command: RecoverTicketCommand) -> OperationResult:
        return await self._tickets.recover_ticket(command)

"""Ticket creation, resident review, escalation, and recovery use cases."""

from dataclasses import asdict, replace

from app.application.errors import AuthorizationFailed, ResourceNotFound
from app.application.events import AggregateType, DomainEventType, build_domain_event
from app.application.models import (
    CreateTicketCommand,
    EscalateTicketCommand,
    OperationResult,
    RecoverTicketCommand,
    ReviewRepairCommand,
)
from app.application.ports import TicketHistoryRecord, UnitOfWork
from app.application.service_support import TransactionalService
from app.domain.enums import ActorType, EscalationDisposition, TicketAction, TicketStatus
from app.domain.invariants import ensure_aggregate_consistency
from app.domain.models import AcceptanceRejection, TicketSnapshot
from app.domain.tickets import (
    EscalationRecoveryRequest,
    TicketCreationRequest,
    TicketTransitionRequest,
    plan_ticket_creation,
    recover_escalated_ticket,
    transition_ticket,
)


class TicketApplicationService(TransactionalService):
    async def create_ticket(self, command: CreateTicketCommand) -> OperationResult:
        async def handler(uow: UnitOfWork, _digest: str) -> OperationResult:
            return await self._create_ticket(uow, command)

        return await self._execute(
            scope="create_ticket",
            metadata=command.metadata,
            payload=asdict(command),
            handler=handler,
        )

    async def _create_ticket(
        self, uow: UnitOfWork, command: CreateTicketCommand
    ) -> OperationResult:
        metadata = command.metadata
        linked = await uow.tickets.resident_has_property(command.resident_id, command.property_id)
        if metadata.actor_type is ActorType.RESIDENT:
            authorized = metadata.actor_id == command.resident_id and linked
        elif metadata.actor_type is ActorType.OPERATOR:
            authorized = linked and await uow.tickets.actor_is_operator(metadata.actor_id)
        else:
            authorized = False
        exact_match = await uow.tickets.has_exact_open_match(
            command.resident_id,
            command.property_id,
            command.issue_category,
            command.issue_location,
        )
        plan = plan_ticket_creation(
            TicketCreationRequest(
                actor_type=metadata.actor_type,
                resident_authorized=authorized,
                issue_valid=bool(
                    command.issue_location.strip() and command.issue_description.strip()
                ),
                duplicate_allowed=(
                    not exact_match
                    or (command.allow_duplicate and metadata.actor_type is ActorType.OPERATOR)
                ),
                idempotency_accepted=True,
            )
        )
        ticket_id = self._id_factory()
        ticket = TicketSnapshot(
            ticket_id=ticket_id,
            resident_id=command.resident_id,
            property_id=command.property_id,
            issue_category=command.issue_category,
            issue_location=command.issue_location.strip(),
            issue_description=command.issue_description.strip(),
            severity=command.severity,
            status=plan.initial_status,
            escalated_from_status=None,
            rework_count=plan.initial_rework_count,
            version=plan.initial_version,
        )
        await uow.tickets.add(ticket)
        await uow.tickets.add_history(
            TicketHistoryRecord(
                ticket_id=ticket_id,
                from_status=None,
                to_status=ticket.status,
                action="CREATE",
                actor_type=metadata.actor_type,
                actor_id=metadata.actor_id,
                trace_id=metadata.trace_id,
                version_before=0,
                version_after=1,
            )
        )
        await uow.outbox.add(
            build_domain_event(
                event_type=DomainEventType.TICKET_CREATED,
                aggregate_type=AggregateType.TICKET,
                aggregate_id=ticket_id,
                aggregate_version=ticket.version,
                metadata=metadata,
                scope="create_ticket",
                payload={
                    "status": ticket.status.value,
                    "issue_category": ticket.issue_category.value,
                    "severity": ticket.severity.value,
                    "property_id": str(ticket.property_id),
                    "resident_id": str(ticket.resident_id),
                },
            )
        )
        return OperationResult(
            ok=True,
            code="TICKET_CREATED",
            resource_type="repair_ticket",
            resource_id=ticket_id,
            resource_version=1,
            data={"status": ticket.status.value},
        )

    async def review_repair(self, command: ReviewRepairCommand) -> OperationResult:
        async def handler(uow: UnitOfWork, _digest: str) -> OperationResult:
            return await self._review_repair(uow, command)

        return await self._execute(
            scope="review_repair",
            metadata=command.metadata,
            payload=asdict(command),
            handler=handler,
        )

    async def _review_repair(
        self, uow: UnitOfWork, command: ReviewRepairCommand
    ) -> OperationResult:
        ticket = await uow.tickets.get(command.ticket_id)
        if ticket is None:
            raise ResourceNotFound("ticket_not_found")
        if (
            command.metadata.actor_type is not ActorType.RESIDENT
            or command.metadata.actor_id != ticket.resident_id
            or not await uow.tickets.resident_has_property(ticket.resident_id, ticket.property_id)
        ):
            raise AuthorizationFailed("resident_not_authorized")
        appointment = await uow.appointments.latest_for_ticket(ticket.ticket_id)
        if appointment is None:
            raise ResourceNotFound("fulfilled_appointment_not_found")
        events = await uow.worker_events.list_for_appointment(appointment.appointment_id)
        ensure_aggregate_consistency(
            ticket.status, (appointment,), events[-1].event_type if events else None
        )
        rejection = None
        action = TicketAction.RESIDENT_ACCEPT
        if not command.accepted:
            action = TicketAction.RESIDENT_REJECT
            if command.rejection_reason is not None:
                rejection = AcceptanceRejection(
                    reason=command.rejection_reason, explanation=command.explanation
                )
        decision = transition_ticket(
            TicketTransitionRequest(
                current_status=ticket.status,
                action=action,
                actor_type=command.metadata.actor_type,
                expected_version=command.expected_ticket_version,
                actual_version=ticket.version,
                resident_authorized=True,
                resident_acceptance=command.accepted,
                acceptance_rejection=rejection,
                current_rework_count=ticket.rework_count,
            )
        )
        updated = replace(
            ticket,
            status=decision.next_status,
            version=decision.next_version,
            rework_count=decision.next_rework_count,
            escalated_from_status=None,
            closed_at=command.metadata.occurred_at if command.accepted else None,
        )
        await uow.tickets.update(updated, expected_version=ticket.version)
        await uow.tickets.add_history(
            self._ticket_history(
                ticket,
                updated,
                action.value,
                command.metadata,
                reason_code=(
                    command.rejection_reason.value if command.rejection_reason is not None else None
                ),
                reason_text=command.explanation,
            )
        )
        await self._emit_ticket_status_changed(
            uow,
            ticket,
            updated,
            action.value,
            command.metadata,
            scope="review_repair",
        )
        return OperationResult(
            ok=True,
            code="REPAIR_ACCEPTED" if command.accepted else "REWORK_REQUESTED",
            resource_type="repair_ticket",
            resource_id=ticket.ticket_id,
            resource_version=updated.version,
            data={"status": updated.status.value, "rework_count": updated.rework_count},
        )

    async def escalate_ticket(self, command: EscalateTicketCommand) -> OperationResult:
        async def handler(uow: UnitOfWork, _digest: str) -> OperationResult:
            return await self._escalate_ticket(uow, command)

        return await self._execute(
            scope="escalate_ticket",
            metadata=command.metadata,
            payload=asdict(command),
            handler=handler,
        )

    async def _escalate_ticket(
        self, uow: UnitOfWork, command: EscalateTicketCommand
    ) -> OperationResult:
        ticket = await uow.tickets.get(command.ticket_id)
        if ticket is None:
            raise ResourceNotFound("ticket_not_found")
        if command.metadata.actor_type is ActorType.OPERATOR:
            if not await uow.tickets.actor_is_operator(command.metadata.actor_id):
                raise AuthorizationFailed("operator_not_authorized")
        elif command.metadata.actor_type is not ActorType.SYSTEM:
            raise AuthorizationFailed("actor_not_allowed")
        decision = transition_ticket(
            TicketTransitionRequest(
                current_status=ticket.status,
                action=TicketAction.ESCALATE,
                actor_type=command.metadata.actor_type,
                expected_version=command.expected_ticket_version,
                actual_version=ticket.version,
                escalation_reason=command.reason_text,
                evidence=command.evidence,
                current_rework_count=ticket.rework_count,
            )
        )
        updated = replace(
            ticket,
            status=decision.next_status,
            escalated_from_status=decision.escalated_from_status,
            version=decision.next_version,
        )
        await uow.tickets.update(updated, expected_version=ticket.version)
        await uow.tickets.add_history(
            self._ticket_history(
                ticket,
                updated,
                TicketAction.ESCALATE.value,
                command.metadata,
                reason_code=command.reason_code,
                reason_text=command.reason_text,
                evidence=command.evidence,
            )
        )
        await self._emit_ticket_status_changed(
            uow,
            ticket,
            updated,
            TicketAction.ESCALATE.value,
            command.metadata,
            scope="escalate_ticket",
        )
        await uow.outbox.add(
            build_domain_event(
                event_type=DomainEventType.TICKET_ESCALATED,
                aggregate_type=AggregateType.TICKET,
                aggregate_id=updated.ticket_id,
                aggregate_version=updated.version,
                metadata=command.metadata,
                scope="escalate_ticket",
                payload={
                    "from_status": ticket.status.value,
                    "to_status": updated.status.value,
                    "reason_code": command.reason_code,
                },
            )
        )
        return OperationResult(
            ok=True,
            code="TICKET_ESCALATED",
            resource_type="repair_ticket",
            resource_id=ticket.ticket_id,
            resource_version=updated.version,
            data={"status": updated.status.value},
        )

    async def recover_ticket(self, command: RecoverTicketCommand) -> OperationResult:
        async def handler(uow: UnitOfWork, _digest: str) -> OperationResult:
            return await self._recover_ticket(uow, command)

        return await self._execute(
            scope="recover_ticket",
            metadata=command.metadata,
            payload=asdict(command),
            handler=handler,
        )

    async def _recover_ticket(
        self, uow: UnitOfWork, command: RecoverTicketCommand
    ) -> OperationResult:
        ticket = await uow.tickets.get(command.ticket_id)
        if ticket is None:
            raise ResourceNotFound("ticket_not_found")
        if (
            command.metadata.actor_type is not ActorType.OPERATOR
            or not await uow.tickets.actor_is_operator(command.metadata.actor_id)
        ):
            raise AuthorizationFailed("operator_not_authorized")
        if command.disposition is EscalationDisposition.APPLY_RESIDENT_ACCEPTANCE:
            raise AuthorizationFailed("resident_acceptance_requires_resident_command")
        appointment = await uow.appointments.latest_for_ticket(ticket.ticket_id)
        events = (
            await uow.worker_events.list_for_appointment(appointment.appointment_id)
            if appointment is not None
            else ()
        )
        decision = recover_escalated_ticket(
            EscalationRecoveryRequest(
                actor_type=command.metadata.actor_type,
                expected_version=command.expected_ticket_version,
                actual_version=ticket.version,
                escalated_from_status=ticket.escalated_from_status,
                appointment_status=appointment.status if appointment is not None else None,
                latest_worker_event=events[-1].event_type if events else None,
                disposition=command.disposition,
                resident_acceptance=command.resident_acceptance,
                rework_recorded=command.rework_recorded,
                conflict_resolved=command.conflict_resolved,
                current_rework_count=ticket.rework_count,
            )
        )
        updated = replace(
            ticket,
            status=decision.next_status,
            escalated_from_status=None,
            version=decision.next_version,
            rework_count=decision.next_rework_count,
            cancelled_at=(
                command.metadata.occurred_at
                if decision.next_status is TicketStatus.CANCELLED
                else ticket.cancelled_at
            ),
            closed_at=(
                command.metadata.occurred_at
                if decision.next_status is TicketStatus.CLOSED
                else ticket.closed_at
            ),
        )
        await uow.tickets.update(updated, expected_version=ticket.version)
        await uow.tickets.add_history(
            self._ticket_history(
                ticket,
                updated,
                f"RECOVER_{command.disposition.value}",
                command.metadata,
                reason_code=command.disposition.value,
                reason_text=command.reason_text,
                evidence=command.evidence,
            )
        )
        await self._emit_ticket_status_changed(
            uow,
            ticket,
            updated,
            f"RECOVER_{command.disposition.value}",
            command.metadata,
            scope="recover_ticket",
        )
        return OperationResult(
            ok=True,
            code="TICKET_RECOVERED",
            resource_type="repair_ticket",
            resource_id=ticket.ticket_id,
            resource_version=updated.version,
            data={"status": updated.status.value},
        )

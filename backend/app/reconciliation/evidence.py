"""Authoritative operation evidence query over the primary business database."""

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.enums import ActorType, TicketStatus
from app.infrastructure.database.models.appointment import Appointment
from app.infrastructure.database.models.idempotency import (
    IdempotencyExecutionStatus,
    IdempotencyRecord,
)
from app.infrastructure.database.models.observability import OutboxEvent
from app.infrastructure.database.models.reconciliation import ReconciliationAction
from app.infrastructure.database.models.ticket import RepairTicket, TicketStatusHistory
from app.infrastructure.database.models.user import ResidentPropertyRelation, User
from app.property_operations.contracts.reconciliation import GetOperationOutcomeRequest
from app.reconciliation.models import ClaimedCase, OperationOutcome, OutcomeStatus
from app.reconciliation.validators import (
    VersionedEntity,
    validate_book_appointment,
    validate_create_ticket,
    validate_escalate_ticket,
    validate_reschedule_appointment,
)


class SqlAlchemyOperationOutcomeQuery:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_operation_outcome(
        self, case: ClaimedCase | GetOperationOutcomeRequest
    ) -> OperationOutcome:
        action = ReconciliationAction(case.action.value)
        async with self._sessions() as session:
            if not await self._authorized(session, case):
                raise OperationOutcomePermissionDenied()
            idem = await session.scalar(
                select(IdempotencyRecord).where(IdempotencyRecord.operation_id == case.operation_id)
            )
            outbox = tuple(
                await session.scalars(
                    select(OutboxEvent).where(OutboxEvent.operation_id == case.operation_id)
                )
            )
            if idem is None and not outbox:
                return OperationOutcome(
                    status=OutcomeStatus.NOT_COMMITTED,
                    operation_id=case.operation_id,
                    action=action,
                    reason_code="AUTHORITATIVE_ABSENCE",
                )
            scope = {
                ReconciliationAction.CREATE_TICKET: "create_ticket",
                ReconciliationAction.BOOK_APPOINTMENT: "book_appointment",
                ReconciliationAction.RESCHEDULE_APPOINTMENT: "reschedule_appointment",
                ReconciliationAction.ESCALATE_TO_OPERATOR: "escalate_ticket",
            }[action]
            validator = {
                ReconciliationAction.CREATE_TICKET: validate_create_ticket,
                ReconciliationAction.BOOK_APPOINTMENT: validate_book_appointment,
                ReconciliationAction.RESCHEDULE_APPOINTMENT: validate_reschedule_appointment,
                ReconciliationAction.ESCALATE_TO_OPERATOR: validate_escalate_ticket,
            }[action]
            entity = None
            ticket: RepairTicket | None = None
            target_entity: RepairTicket | Appointment | None = None
            if idem is not None and idem.resource_id is not None:
                model = (
                    RepairTicket
                    if action
                    in {
                        ReconciliationAction.CREATE_TICKET,
                        ReconciliationAction.ESCALATE_TO_OPERATOR,
                    }
                    else Appointment
                )
                entity = await session.get(model, idem.resource_id)
                if isinstance(entity, RepairTicket):
                    ticket = entity
                elif isinstance(entity, Appointment):
                    ticket = await session.get(RepairTicket, entity.ticket_id)
                if case.target_entity_id is not None:
                    target_model = (
                        Appointment
                        if action is ReconciliationAction.RESCHEDULE_APPOINTMENT
                        else RepairTicket
                    )
                    target_entity = await session.get(target_model, case.target_entity_id)
            typed_entity = cast(RepairTicket | Appointment | None, entity)
            response_payload = (
                idem.response_payload
                if idem is not None and isinstance(idem.response_payload, dict)
                else {}
            )
            response_data = response_payload.get("data")
            response_status = (
                response_data.get("status") if isinstance(response_data, dict) else None
            )
            escalation_history_present = True
            if action is ReconciliationAction.ESCALATE_TO_OPERATOR:
                escalation_history_present = (
                    isinstance(typed_entity, RepairTicket)
                    and case.actor_type is ActorType.OPERATOR
                    and typed_entity.status is TicketStatus.ESCALATED
                    and response_status == TicketStatus.ESCALATED.value
                    and (
                        await session.scalar(
                            select(TicketStatusHistory.id).where(
                                TicketStatusHistory.ticket_id == typed_entity.id,
                                TicketStatusHistory.action == "ESCALATE",
                                TicketStatusHistory.actor_type == ActorType.OPERATOR,
                                TicketStatusHistory.actor_id == str(case.actor_id),
                                TicketStatusHistory.version_after == typed_entity.version,
                                TicketStatusHistory.to_status == TicketStatus.ESCALATED,
                            )
                        )
                    )
                    is not None
                )
            if (
                idem is None
                or idem.execution_status is not IdempotencyExecutionStatus.SUCCEEDED
                or idem.scope != scope
                or idem.actor_type != case.actor_type
                or idem.actor_id != str(case.actor_id)
                or idem.request_hash != case.request_fingerprint
                or idem.response_payload is None
                or idem.resource_id is None
                or typed_entity is None
                or ticket is None
                or ticket.property_id != case.property_id
                or (case.actor_type.value == "RESIDENT" and ticket.resident_id != case.user_id)
                or not self._target_matches(case, action, ticket, typed_entity, target_entity)
                or not escalation_history_present
                or not validator(
                    cast(VersionedEntity, typed_entity),
                    idem.response_payload,
                    {item.event_type for item in outbox},
                )
            ):
                return OperationOutcome(
                    status=OutcomeStatus.INCONSISTENT,
                    operation_id=case.operation_id,
                    action=action,
                    reason_code="EVIDENCE_CONFLICT",
                )
            return OperationOutcome(
                status=OutcomeStatus.COMMITTED,
                operation_id=case.operation_id,
                action=action,
                safe_result={
                    "resource_type": idem.resource_type or "RESOURCE",
                    "resource_id": str(idem.resource_id),
                    "result": idem.response_payload,
                },
                reason_code="DURABLE_EVIDENCE_MATCHED",
            )

    @staticmethod
    async def _authorized(
        session: AsyncSession, case: ClaimedCase | GetOperationOutcomeRequest
    ) -> bool:
        user = await session.get(User, case.user_id)
        if (
            user is None
            or not user.is_active
            or user.id != case.actor_id
            or user.role != case.actor_type.value
        ):
            return False
        if case.actor_type.value == "OPERATOR":
            return True
        if case.actor_type.value != "RESIDENT":
            return False
        relation = await session.scalar(
            select(ResidentPropertyRelation.id).where(
                ResidentPropertyRelation.resident_id == case.user_id,
                ResidentPropertyRelation.property_id == case.property_id,
                ResidentPropertyRelation.is_active.is_(True),
            )
        )
        return relation is not None

    @staticmethod
    def _target_matches(
        case: ClaimedCase | GetOperationOutcomeRequest,
        action: ReconciliationAction,
        ticket: RepairTicket,
        entity: RepairTicket | Appointment,
        target_entity: RepairTicket | Appointment | None,
    ) -> bool:
        if action is ReconciliationAction.CREATE_TICKET:
            return case.target_entity_id in {None, ticket.property_id}
        if action is ReconciliationAction.ESCALATE_TO_OPERATOR:
            return (
                isinstance(target_entity, RepairTicket)
                and target_entity.id == ticket.id
                and entity.id == ticket.id
            )
        if action is ReconciliationAction.BOOK_APPOINTMENT:
            return (
                isinstance(target_entity, RepairTicket)
                and target_entity.id == ticket.id
                and isinstance(entity, Appointment)
            )
        if action is ReconciliationAction.RESCHEDULE_APPOINTMENT:
            return (
                isinstance(entity, Appointment)
                and isinstance(target_entity, Appointment)
                and target_entity.ticket_id == ticket.id
            )
        return False


class OperationOutcomePermissionDenied(RuntimeError):
    """The frozen Case identity no longer authorizes authoritative evidence."""

    code = "PERMISSION_DENIED"

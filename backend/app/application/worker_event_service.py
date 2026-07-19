"""Serialized, append-only Worker Event application use case."""

from dataclasses import asdict, dataclass, replace
from typing import Any, cast

from app.application.errors import (
    AuthorizationFailed,
    IdempotencyConflict,
    ResourceNotFound,
)
from app.application.models import OperationResult, RecordWorkerEventCommand
from app.application.ports import UnitOfWork, WorkerEventRecord
from app.application.service_support import TransactionalService, _json_value
from app.domain.appointments import AppointmentTransitionRequest, transition_appointment
from app.domain.enums import (
    ActorType,
    AppointmentAction,
    AppointmentPurpose,
    CancellationReason,
    FailureDisposition,
    NoShowReason,
    TicketAction,
    TicketStatus,
    WorkerEventType,
    failure_disposition,
)
from app.domain.models import (
    AppointmentSnapshot,
    FailureDetails,
    OutcomeMetadata,
    TicketSnapshot,
    WorkerEventSnapshot,
)
from app.domain.worker_events import WorkerEventRequest, WorkerEventResult, validate_worker_event


@dataclass(frozen=True, slots=True)
class _EventContext:
    ticket: TicketSnapshot
    appointment: AppointmentSnapshot
    prior: tuple[WorkerEventSnapshot, ...]
    decision: WorkerEventResult


class WorkerEventApplicationService(TransactionalService):
    async def record_worker_event(self, command: RecordWorkerEventCommand) -> OperationResult:
        async def handler(uow: UnitOfWork, digest: str) -> OperationResult:
            replay_or_context = await self._load_context(uow, command, digest)
            if isinstance(replay_or_context, OperationResult):
                return replay_or_context
            updated_ticket, updated_appointment = await self._apply_effects(
                uow, command, replay_or_context
            )
            return await self._append_event(
                uow,
                command,
                digest,
                replay_or_context,
                updated_ticket,
                updated_appointment,
            )

        return await self._execute(
            scope="record_worker_event",
            metadata=command.metadata,
            payload=asdict(command),
            handler=handler,
        )

    async def _load_context(
        self, uow: UnitOfWork, command: RecordWorkerEventCommand, digest: str
    ) -> _EventContext | OperationResult:
        appointment = await uow.appointments.get(command.appointment_id, for_update=True)
        if appointment is None or appointment.ticket_id != command.ticket_id:
            raise ResourceNotFound("appointment_not_found")
        prior = tuple(await uow.worker_events.list_for_appointment(appointment.appointment_id))
        existing = await uow.worker_events.get_by_external_key(command.external_event_key)
        if existing is not None:
            if existing.request_hash != digest:
                raise IdempotencyConflict("worker_event_payload_conflict")
            return OperationResult(
                ok=True,
                code="WORKER_EVENT_REPLAYED",
                resource_type="worker_event",
                resource_id=existing.event_id,
                resource_version=existing.sequence_no,
                replayed=True,
            )
        ticket = await uow.tickets.get(command.ticket_id)
        if ticket is None:
            raise ResourceNotFound("ticket_not_found")
        await self._authorize_event_actor(uow, command, appointment)
        return _EventContext(
            ticket=ticket,
            appointment=appointment,
            prior=prior,
            decision=self._validate_event(command, ticket, appointment, prior),
        )

    @staticmethod
    async def _authorize_event_actor(
        uow: UnitOfWork,
        command: RecordWorkerEventCommand,
        appointment: AppointmentSnapshot,
    ) -> None:
        if appointment.worker_id != command.subject_worker_id:
            raise AuthorizationFailed("worker_appointment_mismatch")
        if command.metadata.actor_type is ActorType.WORKER:
            if command.metadata.actor_id != command.subject_worker_id:
                raise AuthorizationFailed("worker_not_authorized")
        elif command.metadata.actor_type is ActorType.OPERATOR:
            if not await uow.tickets.actor_is_operator(command.metadata.actor_id):
                raise AuthorizationFailed("operator_not_authorized")
        elif command.metadata.actor_type is not ActorType.SYSTEM:
            raise AuthorizationFailed("actor_not_allowed")

    @staticmethod
    def _validate_event(
        command: RecordWorkerEventCommand,
        ticket: TicketSnapshot,
        appointment: AppointmentSnapshot,
        prior: tuple[WorkerEventSnapshot, ...],
    ) -> WorkerEventResult:
        failure = None
        if command.failure_reason is not None:
            failure = FailureDetails(
                reason=command.failure_reason,
                worker_statement=command.worker_statement or "",
                evidence=command.evidence,
            )
        return validate_worker_event(
            WorkerEventRequest(
                event_type=command.event_type,
                actor_type=command.metadata.actor_type,
                appointment_id=appointment.appointment_id,
                ticket_status=ticket.status,
                appointment_status=appointment.status,
                appointment_purpose=appointment.purpose,
                current_rework_count=ticket.rework_count,
                expected_ticket_version=command.expected_ticket_version,
                actual_ticket_version=ticket.version,
                expected_appointment_version=command.expected_appointment_version,
                actual_appointment_version=appointment.version,
                prior_events=tuple(item.event_type for item in prior),
                cancellation_reason=command.cancellation_reason,
                no_show_reason=command.no_show_reason,
                failure=failure,
                evidence=command.evidence,
            )
        )

    async def _apply_effects(
        self, uow: UnitOfWork, command: RecordWorkerEventCommand, context: _EventContext
    ) -> tuple[TicketSnapshot, AppointmentSnapshot]:
        ticket = context.ticket
        appointment = context.appointment
        decision = context.decision
        ticket_changed = decision.next_ticket_status is not ticket.status
        appointment_changed = decision.next_appointment_status is not appointment.status
        updated_ticket = replace(
            ticket,
            status=decision.next_ticket_status,
            escalated_from_status=(
                ticket.status if decision.next_ticket_status is TicketStatus.ESCALATED else None
            ),
            rework_count=decision.next_rework_count,
            version=ticket.version + int(ticket_changed),
        )
        updated_appointment = replace(
            appointment,
            status=decision.next_appointment_status,
            version=appointment.version + int(appointment_changed),
        )
        if ticket_changed:
            await uow.tickets.update(updated_ticket, expected_version=ticket.version)
            await uow.tickets.add_history(
                self._ticket_history(
                    ticket,
                    updated_ticket,
                    self._worker_ticket_action(command, appointment.purpose),
                    command.metadata,
                    reason_code=self._worker_reason_code(command),
                    reason_text=command.reason_text or command.worker_statement,
                    evidence=command.evidence,
                )
            )
        if appointment_changed:
            await self._apply_appointment_effect(
                uow, command, ticket, appointment, updated_appointment
            )
        return updated_ticket, updated_appointment

    async def _apply_appointment_effect(
        self,
        uow: UnitOfWork,
        command: RecordWorkerEventCommand,
        ticket: TicketSnapshot,
        appointment: AppointmentSnapshot,
        updated: AppointmentSnapshot,
    ) -> None:
        transition_appointment(
            AppointmentTransitionRequest(
                current_status=appointment.status,
                action=self._appointment_action(command.event_type),
                actor_type=command.metadata.actor_type,
                expected_version=command.expected_appointment_version,
                actual_version=appointment.version,
                outcome=self._outcome_metadata(command, ticket),
            )
        )
        await uow.appointments.update(
            updated,
            expected_version=appointment.version,
            outcome=self._worker_event_outcome(command, ticket),
        )
        await uow.appointments.add_history(
            self._appointment_history(
                appointment,
                updated,
                command.metadata,
                reason_code=self._worker_reason_code(command),
                reason_text=command.reason_text or command.worker_statement,
                evidence=command.evidence,
            )
        )

    async def _append_event(
        self,
        uow: UnitOfWork,
        command: RecordWorkerEventCommand,
        digest: str,
        context: _EventContext,
        ticket: TicketSnapshot,
        appointment: AppointmentSnapshot,
    ) -> OperationResult:
        event_id = self._id_factory()
        sequence_no = len(context.prior) + 1
        await uow.worker_events.add(
            WorkerEventRecord(
                event_id=event_id,
                appointment_id=appointment.appointment_id,
                subject_worker_id=command.subject_worker_id,
                sequence_no=sequence_no,
                event_type=command.event_type.value,
                actor_type=command.metadata.actor_type,
                actor_id=command.metadata.actor_id,
                external_event_key=command.external_event_key,
                request_hash=digest,
                trace_id=command.metadata.trace_id,
                occurred_at=command.metadata.occurred_at,
                payload=cast(dict[str, Any], _json_value(asdict(command))),
            )
        )
        return OperationResult(
            ok=True,
            code="WORKER_EVENT_RECORDED",
            resource_type="worker_event",
            resource_id=event_id,
            resource_version=sequence_no,
            data={
                "ticket_status": ticket.status.value,
                "ticket_version": ticket.version,
                "appointment_status": appointment.status.value,
                "appointment_version": appointment.version,
            },
        )

    @staticmethod
    def _worker_ticket_action(
        command: RecordWorkerEventCommand, purpose: AppointmentPurpose
    ) -> str:
        mapping = {
            WorkerEventType.STARTED: TicketAction.START_WORK,
            WorkerEventType.COMPLETED: TicketAction.COMPLETE_WORK,
            WorkerEventType.NO_SHOW: TicketAction.REVIEWED_NO_SHOW,
        }
        if command.event_type is WorkerEventType.REJECTED:
            return (
                TicketAction.WORKER_REJECTED_REWORK
                if purpose is AppointmentPurpose.REWORK
                else TicketAction.WORKER_REJECTED_INITIAL
            ).value
        if command.event_type is WorkerEventType.CANCELLED:
            return (
                TicketAction.WORKER_CANCELLED_REWORK
                if purpose is AppointmentPurpose.REWORK
                else TicketAction.WORKER_CANCELLED_INITIAL
            ).value
        if command.event_type is WorkerEventType.FAILED_TO_COMPLETE:
            if command.failure_reason is None:
                raise ValueError("failure reason required")
            return (
                TicketAction.FAIL_WORK_REWORK
                if failure_disposition(command.failure_reason) is FailureDisposition.REWORK
                else TicketAction.FAIL_WORK_ESCALATE
            ).value
        return mapping[command.event_type].value

    @staticmethod
    def _appointment_action(event_type: WorkerEventType) -> AppointmentAction:
        if event_type in {WorkerEventType.COMPLETED, WorkerEventType.FAILED_TO_COMPLETE}:
            return AppointmentAction.FULFILL
        if event_type is WorkerEventType.NO_SHOW:
            return AppointmentAction.MARK_NO_SHOW
        return AppointmentAction.CANCEL

    @staticmethod
    def _worker_reason_code(command: RecordWorkerEventCommand) -> str | None:
        value = command.cancellation_reason or command.no_show_reason or command.failure_reason
        return value.value if value is not None else None

    @staticmethod
    def _outcome_metadata(
        command: RecordWorkerEventCommand, ticket: TicketSnapshot
    ) -> OutcomeMetadata | None:
        reason = command.cancellation_reason or command.no_show_reason
        if reason is None:
            return None
        if isinstance(reason, CancellationReason):
            actor_type, actor_id = ActorType.WORKER, command.subject_worker_id
        elif reason is NoShowReason.WORKER_NO_SHOW:
            actor_type, actor_id = ActorType.WORKER, command.subject_worker_id
        else:
            actor_type, actor_id = ActorType.RESIDENT, ticket.resident_id
        return OutcomeMetadata(
            actor_type=actor_type,
            actor_id=actor_id,
            reason_code=reason,
            reason_text=command.reason_text or "",
            evidence=command.evidence,
            occurred_at=command.metadata.occurred_at,
        )

    def _worker_event_outcome(
        self, command: RecordWorkerEventCommand, ticket: TicketSnapshot
    ) -> dict[str, Any] | None:
        outcome = self._outcome_metadata(command, ticket)
        if outcome is None:
            return None
        return {
            "outcome_actor_type": outcome.actor_type,
            "outcome_actor_id": str(outcome.actor_id),
            "outcome_reason_code": outcome.reason_code.value,
            "outcome_reason_text": outcome.reason_text,
            "outcome_evidence": {"items": list(outcome.evidence)},
            "outcome_occurred_at": outcome.occurred_at,
        }

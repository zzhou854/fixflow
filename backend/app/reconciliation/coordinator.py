"""Single coordinator for persisting UNKNOWN_COMMIT cases."""

import hashlib
from uuid import UUID

from app.agent.enums import PendingAction
from app.agent.state import AgentState
from app.agent_runtime.execution_context import current_execution_context
from app.infrastructure.database.models.reconciliation import ReconciliationAction
from app.reconciliation.models import CreateCase, ReconciliationCaseView
from app.reconciliation.ports import ReconciliationRepository

_ACTIONS = {
    PendingAction.CREATE_TICKET: ReconciliationAction.CREATE_TICKET,
    PendingAction.BOOK_APPOINTMENT: ReconciliationAction.BOOK_APPOINTMENT,
    PendingAction.RESCHEDULE_APPOINTMENT: ReconciliationAction.RESCHEDULE_APPOINTMENT,
}


class UnknownCommitCoordinator:
    def __init__(self, repository: ReconciliationRepository) -> None:
        self._repository = repository

    async def create_or_get(self, state: AgentState, operation_id: UUID) -> ReconciliationCaseView:
        operation = state.pending_operation
        context = current_execution_context()
        if operation is None or state.property_id is None or context is None:
            raise RuntimeError("unknown commit lacks durable operation context")
        action = _ACTIONS[operation.action]
        target = (
            state.property_id
            if operation.action is PendingAction.CREATE_TICKET
            else (
                state.active_appointment_id
                if operation.action is PendingAction.RESCHEDULE_APPOINTMENT
                else state.active_ticket_id
            )
        )
        return await self._repository.create_or_get(
            CreateCase(
                operation_id=operation_id,
                action=action,
                thread_id=state.thread_id,
                original_run_id=context.run_id,
                original_trace_id=context.trace_id,
                operation_idempotency_fingerprint=hashlib.sha256(
                    operation.idempotency_key.encode()
                ).hexdigest(),
                request_fingerprint=operation.request_fingerprint,
                actor_type=state.actor_type,
                actor_id=state.actor_id,
                user_id=state.user_id,
                property_id=state.property_id,
                target_entity_type="PROPERTY"
                if operation.action is PendingAction.CREATE_TICKET
                else (
                    "APPOINTMENT"
                    if operation.action is PendingAction.RESCHEDULE_APPOINTMENT
                    else "TICKET"
                ),
                target_entity_id=target,
                expected_entity_version=operation.expected_appointment_version
                or operation.expected_ticket_version,
            )
        )

    async def get(self, case_id: UUID) -> ReconciliationCaseView | None:
        return await self._repository.get(case_id)

    async def create_or_get_operator(
        self,
        command: CreateCase,
    ) -> ReconciliationCaseView:
        """Persist an Operator-only escalation case without a resident thread."""

        if command.action is not ReconciliationAction.ESCALATE_TO_OPERATOR:
            raise ValueError("operator reconciliation only supports escalation")
        return await self._repository.create_or_get(command)

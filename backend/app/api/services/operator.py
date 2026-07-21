"""Operator-only API action adapter over the formal Application facade."""

from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from app.application.models import EscalateTicketCommand, MutationMetadata, OperationResult
from app.application.services import FixFlowApplicationService
from app.domain.enums import ActorType


class OperatorActionService:
    def __init__(self, application: FixFlowApplicationService) -> None:
        self._application = application

    async def escalate(
        self,
        *,
        operator_id: UUID,
        ticket_id: UUID,
        expected_version: int,
        reason_code: str,
        reason_text: str,
        evidence: tuple[str, ...],
        trace_id: UUID,
    ) -> OperationResult:
        key = uuid5(
            NAMESPACE_URL,
            f"fixflow:operator-escalate:{operator_id}:{ticket_id}:{expected_version}:{reason_code}",
        ).hex
        return await self._application.escalate_ticket(
            EscalateTicketCommand(
                metadata=MutationMetadata(
                    actor_type=ActorType.OPERATOR,
                    actor_id=operator_id,
                    trace_id=trace_id,
                    idempotency_key=key,
                    occurred_at=datetime.now(UTC),
                ),
                ticket_id=ticket_id,
                expected_ticket_version=expected_version,
                reason_code=reason_code,
                reason_text=reason_text,
                evidence=evidence,
            )
        )

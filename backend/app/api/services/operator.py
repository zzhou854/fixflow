"""Operator-only escalation at the formal Application mutation boundary."""

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.agent_runtime.mcp.delivery import (
    MutationDeliveryClassification,
    classify_application_mutation_result,
)
from app.application.models import EscalateTicketCommand, MutationMetadata, OperationResult
from app.application.query_models import GetTicketSnapshotQuery, QueryActor
from app.application.services import FixFlowApplicationService
from app.domain.enums import ActorType
from app.fault_injection import FaultInjector, FaultPoint, NoOpFaultInjector
from app.infrastructure.database.models.observability import (
    AgentRunStatus,
    AgentRunTrigger,
    TraceSource,
)
from app.infrastructure.database.models.reconciliation import ReconciliationAction
from app.reconciliation.coordinator import UnknownCommitCoordinator
from app.reconciliation.models import CreateCase, ReconciliationCaseView
from app.trace.models import StartRun, TracePayload
from app.trace.runtime import TraceRuntime


@dataclass(frozen=True, slots=True)
class OperatorEscalationExecution:
    """Safe result of one Operator-only escalation dispatch attempt."""

    operation: OperationResult | None
    run_id: UUID
    reconciliation_case: ReconciliationCaseView | None = None


class OperatorMutationNotSent(RuntimeError):
    """The direct Application mutation was proven not to have been dispatched."""

    code = "NOT_SENT"


class OperatorActionService:
    def __init__(
        self,
        application: FixFlowApplicationService,
        trace: TraceRuntime | None = None,
        *,
        reconciliation: UnknownCommitCoordinator | None = None,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self._application = application
        self._trace = trace
        self._reconciliation = reconciliation
        self._faults = fault_injector or NoOpFaultInjector()

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
        idempotency_key: str,
        request_fingerprint: str,
    ) -> OperatorEscalationExecution:
        """Dispatch once; any post-dispatch uncertainty becomes a Case."""

        snapshot = await self._application.get_ticket_snapshot(
            GetTicketSnapshotQuery(
                actor=QueryActor(ActorType.OPERATOR, operator_id), ticket_id=ticket_id
            )
        )
        operation_id = uuid5(
            NAMESPACE_URL,
            f"fixflow:operator-escalate:{operator_id}:{idempotency_key.strip()}",
        )
        run_id = uuid4()
        occurred_at = datetime.now(UTC)
        if self._trace is not None:
            await self._trace.start_run(
                StartRun(
                    run_id=run_id,
                    thread_id=None,
                    trace_id=trace_id,
                    trigger=AgentRunTrigger.OPERATOR_ACTION,
                    actor_type=ActorType.OPERATOR.value,
                    actor_id=operator_id,
                    user_id=operator_id,
                    property_id=snapshot.property_id,
                    started_at=occurred_at,
                )
            )
        try:
            await self._faults.hit(operation_id, FaultPoint.BEFORE_MUTATION_DISPATCH)
        except Exception as exc:
            if self._trace is not None:
                await self._trace.finish_run(
                    run_id,
                    status=AgentRunStatus.FAILED_SAFE,
                    occurred_at=datetime.now(UTC),
                    error_code="NOT_SENT",
                )
            raise OperatorMutationNotSent("operator mutation was not dispatched") from exc
        try:
            result = await self._application.escalate_ticket(
                EscalateTicketCommand(
                    metadata=MutationMetadata(
                        actor_type=ActorType.OPERATOR,
                        actor_id=operator_id,
                        trace_id=trace_id,
                        idempotency_key=idempotency_key,
                        occurred_at=occurred_at,
                        run_id=run_id if self._trace is not None else None,
                        operation_id=operation_id,
                        request_fingerprint=request_fingerprint,
                    ),
                    ticket_id=ticket_id,
                    expected_ticket_version=expected_version,
                    reason_code=reason_code,
                    reason_text=reason_text,
                    evidence=evidence,
                )
            )
            await self._faults.hit(operation_id, FaultPoint.AFTER_MUTATION_DISPATCH)
            if result.ok:
                await self._faults.hit(operation_id, FaultPoint.AFTER_COMMIT_BEFORE_RESULT)
            await self._faults.hit(operation_id, FaultPoint.AFTER_RESULT_RECEIVED_BEFORE_VALIDATION)
        except Exception:
            return await self._unknown(
                operator_id=operator_id,
                ticket_id=ticket_id,
                property_id=snapshot.property_id,
                expected_version=expected_version,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                run_id=run_id,
                trace_id=trace_id,
            )

        classification = classify_application_mutation_result(ok=result.ok, code=result.code)
        if classification is MutationDeliveryClassification.UNKNOWN_COMMIT:
            return await self._unknown(
                operator_id=operator_id,
                ticket_id=ticket_id,
                property_id=snapshot.property_id,
                expected_version=expected_version,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                run_id=run_id,
                trace_id=trace_id,
            )
        if classification is MutationDeliveryClassification.KNOWN_SUCCESS and (
            result.resource_id != ticket_id
            or result.resource_version is None
            or result.data.get("status") != "ESCALATED"
        ):
            return await self._unknown(
                operator_id=operator_id,
                ticket_id=ticket_id,
                property_id=snapshot.property_id,
                expected_version=expected_version,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                run_id=run_id,
                trace_id=trace_id,
            )
        try:
            await self._faults.hit(
                operation_id, FaultPoint.AFTER_RESULT_VALIDATED_BEFORE_RUN_FINALIZATION
            )
        except Exception:
            return await self._unknown(
                operator_id=operator_id,
                ticket_id=ticket_id,
                property_id=snapshot.property_id,
                expected_version=expected_version,
                operation_id=operation_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                run_id=run_id,
                trace_id=trace_id,
            )
        if self._trace is not None:
            await self._trace.finish_run(
                run_id,
                status=(AgentRunStatus.COMPLETED if result.ok else AgentRunStatus.FAILED_SAFE),
                occurred_at=datetime.now(UTC),
                error_code=None if result.ok else result.code,
            )
        return OperatorEscalationExecution(operation=result, run_id=run_id)

    async def _unknown(
        self,
        *,
        operator_id: UUID,
        ticket_id: UUID,
        property_id: UUID,
        expected_version: int,
        operation_id: UUID,
        idempotency_key: str,
        request_fingerprint: str,
        run_id: UUID,
        trace_id: UUID,
    ) -> OperatorEscalationExecution:
        if self._trace is not None:
            await self._trace.finish_run(
                run_id,
                status=AgentRunStatus.FAILED_SAFE,
                occurred_at=datetime.now(UTC),
                error_code="UNKNOWN_COMMIT",
            )
        if self._reconciliation is None:
            raise RuntimeError("operator reconciliation is not configured")
        case = await self._reconciliation.create_or_get_operator(
            CreateCase(
                operation_id=operation_id,
                action=ReconciliationAction.ESCALATE_TO_OPERATOR,
                thread_id=None,
                original_run_id=run_id,
                original_trace_id=trace_id,
                operation_idempotency_fingerprint=hashlib.sha256(
                    idempotency_key.strip().encode()
                ).hexdigest(),
                request_fingerprint=request_fingerprint,
                actor_type=ActorType.OPERATOR,
                actor_id=operator_id,
                user_id=operator_id,
                property_id=property_id,
                target_entity_type="TICKET",
                target_entity_id=ticket_id,
                expected_entity_version=expected_version,
            )
        )
        return OperatorEscalationExecution(operation=None, run_id=run_id, reconciliation_case=case)

    async def record_api_replay(
        self,
        result: OperatorEscalationExecution,
        *,
        trace_id: UUID,
        idempotency_key_fingerprint: str,
    ) -> None:
        if self._trace is None:
            return
        await self._trace.append_event(
            event_key=self._trace.event_key(trace_id, "api_request_replayed"),
            run_id=None,
            thread_id=None,
            trace_id=trace_id,
            source=TraceSource.API,
            event_type="api_request_replayed",
            payload=TracePayload(
                original_run_id=result.run_id,
                idempotency_key_fingerprint=idempotency_key_fingerprint,
                replayed=True,
            ),
            occurred_at=datetime.now(UTC),
        )

    async def refresh_reconciliation(
        self, result: OperatorEscalationExecution
    ) -> OperatorEscalationExecution:
        """Refresh the formal case verdict without redispatching the mutation."""

        if result.reconciliation_case is None or self._reconciliation is None:
            return result
        current = await self._reconciliation.get(result.reconciliation_case.id)
        return replace(result, reconciliation_case=current or result.reconciliation_case)

    async def record_api_conflict(
        self, *, trace_id: UUID, idempotency_key_fingerprint: str
    ) -> None:
        if self._trace is None:
            return
        await self._trace.append_event(
            event_key=self._trace.event_key(trace_id, "api_request_conflict"),
            run_id=None,
            thread_id=None,
            trace_id=trace_id,
            source=TraceSource.API,
            event_type="api_request_conflict",
            payload=TracePayload(idempotency_key_fingerprint=idempotency_key_fingerprint),
            occurred_at=datetime.now(UTC),
        )

"""Operator-only escalation at the formal Application mutation boundary."""

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.agent.enums import AgentIntent, PendingAction
from app.agent.state import CachedAppointmentSnapshot, CachedTicketSnapshot
from app.agent_runtime.mcp.delivery import (
    MutationDeliveryClassification,
    classify_application_mutation_result,
)
from app.application.models import EscalateTicketCommand, MutationMetadata, OperationResult
from app.application.query_models import (
    GetTicketSnapshotQuery,
    QueryActor,
    TicketSnapshotReadModel,
)
from app.application.services import FixFlowApplicationService
from app.domain.enums import ActorType, WorkflowStage
from app.fault_injection import FaultInjector, FaultPoint, NoOpFaultInjector
from app.infrastructure.database.models.observability import (
    AgentRunStatus,
    AgentRunTrigger,
    TraceSource,
)
from app.infrastructure.database.models.reconciliation import ReconciliationAction
from app.policy.enums import EvidenceSufficiency
from app.reconciliation.coordinator import UnknownCommitCoordinator
from app.reconciliation.models import CreateCase, ReconciliationCaseView
from app.replay.capture import ReplayCaptureService
from app.replay.models import OperatorActionReplayInput, ReplaySafeAgentState
from app.replay.steps import OperatorMutationResultStep
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
        replay: ReplayCaptureService | None = None,
    ) -> None:
        self._application = application
        self._trace = trace
        self._reconciliation = reconciliation
        self._faults = fault_injector or NoOpFaultInjector()
        self._replay = replay

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
        replay_input = OperatorActionReplayInput(
            action="ESCALATE_TO_OPERATOR",
            target_entity_id=ticket_id,
            expected_version=expected_version,
            request_fingerprint=request_fingerprint,
        )
        replay_state = self._replay_state(
            operator_id=operator_id,
            trace_id=trace_id,
            snapshot=snapshot,
            observed_at=occurred_at,
        )
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
                replay_input=replay_input,
                replay_state=replay_state,
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
                replay_input=replay_input,
                replay_state=replay_state,
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
                replay_input=replay_input,
                replay_state=replay_state,
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
                replay_input=replay_input,
                replay_state=replay_state,
            )
        if self._trace is not None:
            await self._trace.finish_run(
                run_id,
                status=(AgentRunStatus.COMPLETED if result.ok else AgentRunStatus.FAILED_SAFE),
                occurred_at=datetime.now(UTC),
                error_code=None if result.ok else result.code,
            )
        await self._capture_replay(
            run_id=run_id,
            trace_id=trace_id,
            input_envelope=replay_input,
            state=replay_state,
            mutation=OperatorMutationResultStep(
                action="ESCALATE_TO_OPERATOR",
                operation_id=operation_id,
                request_fingerprint=request_fingerprint,
                delivery_classification=classification.value,
                resource_id=result.resource_id,
                resource_version=result.resource_version,
                business_error_code=None if result.ok else result.code,
            ),
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
        replay_input: OperatorActionReplayInput,
        replay_state: ReplaySafeAgentState,
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
        await self._capture_replay(
            run_id=run_id,
            trace_id=trace_id,
            input_envelope=replay_input,
            state=replay_state,
            mutation=OperatorMutationResultStep(
                action="ESCALATE_TO_OPERATOR",
                operation_id=operation_id,
                request_fingerprint=request_fingerprint,
                delivery_classification="UNKNOWN_COMMIT",
                resource_id=ticket_id,
                business_error_code="UNKNOWN_COMMIT",
                reconciliation_case_id=case.id,
            ),
        )
        return OperatorEscalationExecution(operation=None, run_id=run_id, reconciliation_case=case)

    async def _capture_replay(
        self,
        *,
        run_id: UUID,
        trace_id: UUID,
        input_envelope: OperatorActionReplayInput,
        state: ReplaySafeAgentState,
        mutation: OperatorMutationResultStep,
    ) -> None:
        if self._replay is None:
            return
        await self._replay.capture_operator_action(
            original_run_id=run_id,
            original_trace_id=trace_id,
            input_envelope=input_envelope,
            start_state=state,
            mutation=mutation,
        )

    @staticmethod
    def _replay_state(
        *,
        operator_id: UUID,
        trace_id: UUID,
        snapshot: TicketSnapshotReadModel,
        observed_at: datetime,
    ) -> ReplaySafeAgentState:
        active = snapshot.active_appointment
        cached_appointment = (
            CachedAppointmentSnapshot(
                appointment_id=active.appointment_id,
                worker_id=active.worker_id,
                appointment_status=active.status,
                scheduled_start=active.scheduled_start,
                scheduled_end=active.scheduled_end,
                appointment_version=active.appointment_version,
            )
            if active is not None
            else None
        )
        cached = CachedTicketSnapshot(
            ticket_id=snapshot.ticket_id,
            ticket_version=snapshot.ticket_version,
            ticket_status=snapshot.ticket_status,
            severity=snapshot.severity,
            rework_count=snapshot.rework_count,
            active_appointment=cached_appointment,
            observed_at=observed_at,
        )
        return ReplaySafeAgentState(
            thread_id=None,
            trace_id=trace_id,
            actor_type=ActorType.OPERATOR,
            actor_id=operator_id,
            user_id=operator_id,
            property_id=snapshot.property_id,
            property_context_verified=True,
            workflow_stage=WorkflowStage.HUMAN_REVIEW,
            task_intent=AgentIntent.REQUEST_HUMAN,
            utterance_intent=AgentIntent.REQUEST_HUMAN,
            intent_version=1,
            issue_category=snapshot.issue_category,
            issue_location=snapshot.issue_location,
            normalized_issue_location=" ".join(snapshot.issue_location.casefold().split()),
            safe_issue_summary=f"{snapshot.issue_category.value}:{snapshot.issue_location}",
            severity=snapshot.severity,
            safety_review_required=False,
            policy_sufficiency=EvidenceSufficiency.SUFFICIENT,
            policy_conflict=False,
            active_ticket_id=snapshot.ticket_id,
            active_appointment_id=(active.appointment_id if active is not None else None),
            active_ticket_snapshot=cached,
            ticket_snapshot_version=snapshot.ticket_version,
            appointment_version=(active.appointment_version if active is not None else None),
            pending_action=PendingAction.NONE,
            reference_time=observed_at,
            timezone_name="Asia/Shanghai",
        )

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

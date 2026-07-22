"""Operator-only API action adapter over the formal Application facade."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from app.application.models import EscalateTicketCommand, MutationMetadata, OperationResult
from app.application.services import FixFlowApplicationService
from app.domain.enums import ActorType
from app.infrastructure.database.models.observability import (
    AgentRunStatus,
    AgentRunTrigger,
    TraceSource,
)
from app.trace.models import StartRun, TracePayload
from app.trace.runtime import TraceRuntime


class OperatorActionService:
    def __init__(
        self, application: FixFlowApplicationService, trace: TraceRuntime | None = None
    ) -> None:
        self._application = application
        self._trace = trace

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
        run_id = uuid4()
        operation_id = uuid5(NAMESPACE_URL, f"fixflow:operation:{key}")
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
                    started_at=occurred_at,
                )
            )
        try:
            result = await self._application.escalate_ticket(
                EscalateTicketCommand(
                    metadata=MutationMetadata(
                        actor_type=ActorType.OPERATOR,
                        actor_id=operator_id,
                        trace_id=trace_id,
                        idempotency_key=key,
                        occurred_at=occurred_at,
                        run_id=run_id if self._trace is not None else None,
                        operation_id=operation_id,
                    ),
                    ticket_id=ticket_id,
                    expected_ticket_version=expected_version,
                    reason_code=reason_code,
                    reason_text=reason_text,
                    evidence=evidence,
                )
            )
        except Exception:
            if self._trace is not None:
                await self._trace.finish_run(
                    run_id,
                    status=AgentRunStatus.FAILED,
                    occurred_at=datetime.now(UTC),
                    error_code="INTERNAL_ERROR",
                )
            raise
        if self._trace is not None:
            await self._trace.finish_run(
                run_id,
                status=(AgentRunStatus.COMPLETED if result.ok else AgentRunStatus.FAILED_SAFE),
                occurred_at=datetime.now(UTC),
                error_code=None if result.ok else result.code,
            )
            # This adapter-only marker is intentionally excluded from API and
            # MCP result schemas. It lets an HTTP replay point to the original
            # Run without exposing a second business identifier.
            result = replace(result, data={**result.data, "_trace_run_id": str(run_id)})
        return result

    async def record_api_replay(
        self,
        result: OperationResult,
        *,
        trace_id: UUID,
        idempotency_key_fingerprint: str,
    ) -> None:
        if self._trace is None:
            return
        raw_run_id = result.data.get("_trace_run_id")
        original_run_id = UUID(str(raw_run_id)) if raw_run_id is not None else None
        await self._trace.append_event(
            event_key=self._trace.event_key(trace_id, "api_request_replayed"),
            run_id=None,
            thread_id=None,
            trace_id=trace_id,
            source=TraceSource.API,
            event_type="api_request_replayed",
            payload=TracePayload(
                original_run_id=original_run_id,
                idempotency_key_fingerprint=idempotency_key_fingerprint,
                replayed=True,
            ),
            occurred_at=datetime.now(UTC),
        )

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

"""Ticket-linked operator authorization over sanitized persistent traces."""

from uuid import UUID, uuid4

from app.api.schemas.trace import AgentRunResponse, TraceEventResponse
from app.api.services.operator_review import OperatorThreadReviewService
from app.application.auth import AuthenticatedIdentity
from app.infrastructure.database.models.observability import TraceSource
from app.trace.models import AgentRunRecord
from app.trace.runtime import TraceRuntime


class OperatorTraceQueryService:
    def __init__(
        self,
        review: OperatorThreadReviewService,
        trace: TraceRuntime,
    ) -> None:
        self._review = review
        self._trace = trace

    async def list_runs(
        self,
        identity: AuthenticatedIdentity,
        thread_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[AgentRunResponse, ...] | None:
        if await self._review.review(identity, thread_id=thread_id, trace_id=uuid4()) is None:
            return None
        rows = await self._trace.list_runs(thread_id, limit=limit, offset=offset)
        return tuple(self._run_response(row) for row in rows)

    async def get_run(
        self, identity: AuthenticatedIdentity, run_id: UUID
    ) -> AgentRunResponse | None:
        row = await self._trace.get_run(run_id)
        if row is None or row.thread_id is None:
            return None
        if await self._review.review(identity, thread_id=row.thread_id, trace_id=uuid4()) is None:
            return None
        return self._run_response(row)

    async def list_events(
        self,
        identity: AuthenticatedIdentity,
        run_id: UUID,
        *,
        source: TraceSource | None,
        limit: int,
        offset: int,
    ) -> tuple[TraceEventResponse, ...] | None:
        if await self.get_run(identity, run_id) is None:
            return None
        rows = await self._trace.list_events(run_id, source=source, limit=limit, offset=offset)
        return tuple(
            TraceEventResponse.model_validate(
                {
                    "event_id": row.event_id,
                    "sequence_number": row.sequence_number,
                    "source": row.source,
                    "event_type": row.event_type,
                    "node_name": row.node_name,
                    "operation_id": row.operation_id,
                    "payload": row.payload,
                    "occurred_at": row.occurred_at,
                }
            )
            for row in rows
        )

    @staticmethod
    def _run_response(row: AgentRunRecord) -> AgentRunResponse:
        return AgentRunResponse(
            run_id=row.run_id,
            thread_id=row.thread_id,
            trace_id=row.trace_id,
            trigger=row.trigger,
            status=row.status,
            started_at=row.started_at,
            finished_at=row.finished_at,
            error_code=row.error_code,
        )

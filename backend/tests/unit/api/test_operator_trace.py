from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.api.services.operator_review import OperatorThreadReviewService
from app.api.services.operator_trace import OperatorTraceQueryService
from app.application.auth import AuthenticatedIdentity
from app.domain.enums import ActorType
from app.infrastructure.database.models.observability import (
    AgentRunStatus,
    AgentRunTrigger,
    TraceSource,
)
from app.trace.models import AgentRunRecord, TraceEventRecord
from app.trace.runtime import TraceRuntime


def _identity() -> AuthenticatedIdentity:
    return AuthenticatedIdentity(uuid4(), "operator", ActorType.OPERATOR)


@pytest.mark.asyncio
async def test_unlinked_thread_is_not_enumerated_by_operator_trace() -> None:
    review = AsyncMock()
    review.review.return_value = None
    trace = AsyncMock()
    service = OperatorTraceQueryService(
        cast(OperatorThreadReviewService, review), cast(TraceRuntime, trace)
    )
    assert await service.list_runs(_identity(), uuid4(), limit=20, offset=0) is None
    trace.list_runs.assert_not_awaited()


@pytest.mark.asyncio
async def test_linked_operator_receives_only_sanitized_run_and_event_projection() -> None:
    review = AsyncMock()
    review.review.return_value = object()
    trace = AsyncMock()
    now = datetime.now(UTC)
    thread_id, run_id, trace_id = uuid4(), uuid4(), uuid4()
    trace.get_run.return_value = AgentRunRecord.model_validate(
        {
            "id": run_id,
            "thread_id": thread_id,
            "trace_id": trace_id,
            "trigger": AgentRunTrigger.MESSAGE,
            "status": AgentRunStatus.COMPLETED,
            "actor_type": "RESIDENT",
            "actor_id": uuid4(),
            "user_id": uuid4(),
            "property_id": uuid4(),
            "initial_intent_version": 1,
            "final_intent_version": 1,
            "started_at": now,
            "finished_at": now,
            "terminal_event_type": "run_completed",
            "error_code": None,
        }
    )
    trace.list_events.return_value = (
        TraceEventRecord.model_validate(
            {
                "id": uuid4(),
                "event_key": "internal-key-not-returned",
                "run_id": run_id,
                "thread_id": thread_id,
                "trace_id": trace_id,
                "sequence_number": 1,
                "source": TraceSource.AGENT,
                "event_type": "run_started",
                "node_name": None,
                "operation_id": None,
                "payload": {"run_status": "RUNNING"},
                "occurred_at": now,
            }
        ),
    )
    service = OperatorTraceQueryService(
        cast(OperatorThreadReviewService, review), cast(TraceRuntime, trace)
    )
    events = await service.list_events(_identity(), run_id, source=None, limit=20, offset=0)
    assert events is not None
    response = events[0].model_dump()
    assert response["event_type"] == "run_started"
    assert "event_key" not in response
    assert "trace_id" not in response
    assert "conversation_messages" not in response
    assert "checkpoint" not in response

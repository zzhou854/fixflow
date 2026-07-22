from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query

from app.api.dependencies import ApiServices, get_services, require_operator
from app.api.errors import ApiError
from app.api.schemas.agent import OperatorThreadResponse
from app.api.schemas.tickets import (
    EscalateTicketRequest,
    OperationResponse,
    TicketDetailResponse,
    TicketListItemResponse,
    TicketPageResponse,
)
from app.api.schemas.trace import (
    AgentRunPageResponse,
    AgentRunResponse,
    TraceEventPageResponse,
)
from app.application.auth import AuthenticatedIdentity
from app.application.query_models import QueryActor
from app.domain.enums import IssueCategory, Severity, TicketStatus
from app.infrastructure.database.models.observability import TraceSource

router = APIRouter(prefix="/api/v1/operator", tags=["operator"])


@router.get("/threads/{thread_id}/runs", response_model=AgentRunPageResponse)
async def list_thread_runs(
    thread_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> AgentRunPageResponse:
    if services.operator_trace is None:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "执行审计暂不可用。", retryable=True)
    rows = await services.operator_trace.list_runs(identity, thread_id, limit=limit, offset=offset)
    if rows is None:
        raise ApiError(404, "NOT_FOUND", "未找到已关联工单的会话。")
    return AgentRunPageResponse(items=rows, limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=AgentRunResponse)
async def get_run(
    run_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> AgentRunResponse:
    if services.operator_trace is None:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "执行审计暂不可用。", retryable=True)
    row = await services.operator_trace.get_run(identity, run_id)
    if row is None:
        raise ApiError(404, "NOT_FOUND", "未找到已授权的执行记录。")
    return row


@router.get("/runs/{run_id}/events", response_model=TraceEventPageResponse)
async def list_run_events(
    run_id: UUID,
    source: TraceSource | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> TraceEventPageResponse:
    if services.operator_trace is None:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "执行审计暂不可用。", retryable=True)
    rows = await services.operator_trace.list_events(
        identity, run_id, source=source, limit=limit, offset=offset
    )
    if rows is None:
        raise ApiError(404, "NOT_FOUND", "未找到已授权的执行事件。")
    return TraceEventPageResponse(items=rows, limit=limit, offset=offset)


@router.get("/tickets", response_model=TicketPageResponse)
async def list_tickets(
    ticket_status: TicketStatus | None = None,
    issue_category: IssueCategory | None = None,
    severity: Severity | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> TicketPageResponse:
    items = await services.application.list_operator_tickets(
        QueryActor(identity.actor_type, identity.actor_id),
        ticket_status=ticket_status,
        issue_category=issue_category,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    return TicketPageResponse(
        items=tuple(TicketListItemResponse.model_validate(item) for item in items),
        limit=limit,
        offset=offset,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(
    ticket_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> TicketDetailResponse:
    detail = await services.application.get_ticket_detail(
        QueryActor(identity.actor_type, identity.actor_id), ticket_id
    )
    return TicketDetailResponse.model_validate(detail)


@router.get("/threads/{thread_id}", response_model=OperatorThreadResponse)
async def get_thread(
    thread_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> OperatorThreadResponse:
    result = await services.operator_review.review(identity, thread_id=thread_id, trace_id=uuid4())
    if result is None:
        raise ApiError(404, "NOT_FOUND", "未找到会话。")
    return result


@router.post("/tickets/{ticket_id}/escalate", response_model=OperationResponse)
async def escalate(
    ticket_id: UUID,
    request: EscalateTicketRequest,
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
) -> OperationResponse:
    replay_trace_id = uuid4()
    key_fingerprint = services.idempotency.key_fingerprint(idempotency_key)
    result = await services.idempotency.execute(
        caller_id=identity.actor_id,
        scope=f"operator:escalate:{ticket_id}",
        key=idempotency_key,
        payload=request.model_dump(mode="json"),
        operation=lambda: services.operator_actions.escalate(
            operator_id=identity.actor_id,
            ticket_id=ticket_id,
            expected_version=request.expected_version,
            reason_code=request.reason_code,
            reason_text=request.reason_text,
            evidence=request.evidence,
            trace_id=uuid4(),
        ),
        on_replay=lambda result: services.operator_actions.record_api_replay(
            result,
            trace_id=replay_trace_id,
            idempotency_key_fingerprint=key_fingerprint,
        ),
        on_conflict=lambda: services.operator_actions.record_api_conflict(
            trace_id=replay_trace_id,
            idempotency_key_fingerprint=key_fingerprint,
        ),
    )
    return OperationResponse.model_validate(result)

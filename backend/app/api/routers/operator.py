from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import JSONResponse

from app.api.dependencies import ApiServices, get_services, require_operator
from app.api.errors import ApiError
from app.api.schemas.agent import OperatorThreadResponse
from app.api.schemas.reconciliation import ReconciliationCasePage, ReconciliationCaseResponse
from app.api.schemas.replay import (
    ReplayBundleResponse,
    ReplayExecutionResponse,
    ReplayRunDetailResponse,
    ReplayRunPageResponse,
)
from app.api.schemas.tickets import (
    EscalateTicketRequest,
    OperationResponse,
    ReconciliationPendingResponse,
    TicketDetailResponse,
    TicketListItemResponse,
    TicketPageResponse,
)
from app.api.schemas.trace import (
    AgentRunPageResponse,
    AgentRunResponse,
    TraceEventPageResponse,
)
from app.api.services.operator import OperatorEscalationExecution, OperatorMutationNotSent
from app.application.auth import AuthenticatedIdentity
from app.application.query_models import QueryActor
from app.domain.enums import IssueCategory, Severity, TicketStatus
from app.infrastructure.database.models.observability import TraceSource
from app.infrastructure.database.models.reconciliation import (
    ReconciliationAction,
    ReconciliationStatus,
)
from app.replay.repository import ReplayConflict, ReplayIdempotencyConflict

router = APIRouter(prefix="/api/v1/operator", tags=["operator"])


@router.get("/threads/{thread_id}/replay-runs", response_model=ReplayRunPageResponse)
async def list_replay_runs(
    thread_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> ReplayRunPageResponse:
    if services.operator_replay is None:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "重放验证暂不可用。", retryable=True)
    rows = await services.operator_replay.list_for_thread(
        identity, thread_id, limit=limit, offset=offset
    )
    if rows is None:
        raise ApiError(404, "NOT_FOUND", "未找到已授权的会话执行记录。")
    return ReplayRunPageResponse(items=rows, limit=limit, offset=offset)


@router.get("/runs/{run_id}/replay", response_model=ReplayRunDetailResponse)
async def get_replay_run(
    run_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> ReplayRunDetailResponse:
    row = (
        await services.operator_replay.get_run(identity, run_id)
        if services.operator_replay
        else None
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "未找到已授权的重放记录。")
    return row


@router.get("/replay-bundles/{bundle_id}", response_model=ReplayBundleResponse)
async def get_replay_bundle(
    bundle_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> ReplayBundleResponse:
    row = (
        await services.operator_replay.get_bundle(identity, bundle_id)
        if services.operator_replay
        else None
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "未找到已授权的重放证据。")
    return row


@router.post("/runs/{run_id}/replay/verify", response_model=ReplayExecutionResponse)
async def verify_replay(
    run_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
) -> ReplayExecutionResponse:
    if services.operator_replay is None:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "重放验证暂不可用。", retryable=True)
    try:
        row = await services.operator_replay.verify(
            identity, run_id, idempotency_key=idempotency_key
        )
    except ReplayIdempotencyConflict as exc:
        raise ApiError(409, "IDEMPOTENCY_CONFLICT", "该幂等键已用于其他验证请求。") from exc
    except ReplayConflict as exc:
        raise ApiError(409, "REPLAY_NOT_READY", "该执行缺少完整重放证据。") from exc
    if row is None:
        raise ApiError(404, "NOT_FOUND", "未找到已授权的重放记录。")
    return row


@router.get(
    "/replay-executions/{execution_id}",
    response_model=ReplayExecutionResponse,
)
async def get_replay_execution(
    execution_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> ReplayExecutionResponse:
    row = (
        await services.operator_replay.get_execution(identity, execution_id)
        if services.operator_replay
        else None
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "未找到已授权的验证结果。")
    return row


@router.get("/reconciliation/cases", response_model=ReconciliationCasePage)
async def list_reconciliation_cases(
    status: ReconciliationStatus | None = None,
    operation: ReconciliationAction | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> ReconciliationCasePage:
    if services.operator_reconciliation is None:
        raise ApiError(503, "SERVICE_UNAVAILABLE", "对账服务暂不可用。", retryable=True)
    return ReconciliationCasePage(
        items=await services.operator_reconciliation.list(
            operator_id=identity.actor_id,
            status=status,
            action=operation,
            limit=limit,
            offset=offset,
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/reconciliation/cases/{case_id}", response_model=ReconciliationCaseResponse)
async def get_reconciliation_case(
    case_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> ReconciliationCaseResponse:
    row = (
        await services.operator_reconciliation.get(case_id, operator_id=identity.actor_id)
        if services.operator_reconciliation
        else None
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "未找到对账记录。")
    return row


@router.post("/reconciliation/cases/{case_id}/recheck", response_model=ReconciliationCaseResponse)
async def recheck_reconciliation_case(
    case_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_operator),
    services: ApiServices = Depends(get_services),
) -> ReconciliationCaseResponse:
    row = (
        await services.operator_reconciliation.recheck(case_id, operator_id=identity.actor_id)
        if services.operator_reconciliation
        else None
    )
    if row is None:
        raise ApiError(409, "RECONCILIATION_NOT_RECHECKABLE", "该对账记录不能重新检查。")
    return row


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
) -> OperationResponse | JSONResponse:
    replay_trace_id = uuid4()
    key_fingerprint = services.idempotency.key_fingerprint(idempotency_key)
    payload = request.model_dump(mode="json")
    scope = f"operator:escalate:{ticket_id}"

    async def dispatch() -> OperatorEscalationExecution:
        return await services.operator_actions.escalate(
            operator_id=identity.actor_id,
            ticket_id=ticket_id,
            expected_version=request.expected_version,
            reason_code=request.reason_code,
            reason_text=request.reason_text,
            evidence=request.evidence,
            trace_id=uuid4(),
            idempotency_key=idempotency_key,
            request_fingerprint=services.idempotency.payload_fingerprint(payload),
        )

    async def execute_once() -> OperatorEscalationExecution:
        return await services.idempotency.execute(
            caller_id=identity.actor_id,
            scope=scope,
            key=idempotency_key,
            payload=payload,
            operation=dispatch,
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

    try:
        execution = await execute_once()
        execution = await services.operator_actions.refresh_reconciliation(execution)
        case = execution.reconciliation_case
        if case is not None and case.status is ReconciliationStatus.RESOLVED_NOT_COMMITTED:
            released = await services.idempotency.discard_completed(
                caller_id=identity.actor_id,
                scope=scope,
                key=idempotency_key,
                payload=payload,
            )
            if released:
                execution = await execute_once()
    except OperatorMutationNotSent as exc:
        raise ApiError(
            503,
            "NOT_SENT",
            "升级请求尚未发出，可使用原请求重试。",
            retryable=True,
        ) from exc
    if execution.reconciliation_case is not None:
        case = execution.reconciliation_case
        response = OperationResponse(
            ok=False,
            code="RECONCILIATION_PENDING",
            resource_type="repair_ticket",
            resource_id=ticket_id,
            resource_version=None,
            replayed=False,
            reconciliation=ReconciliationPendingResponse(
                case_id=case.id,
                action=case.action,
                status=case.status,
                retry_allowed=False,
                ticket_id=ticket_id,
            ),
        )
        return JSONResponse(status_code=202, content=response.model_dump(mode="json"))
    if execution.operation is None:
        raise ApiError(500, "INTERNAL_ERROR", "升级结果不可用。", retryable=True)
    if not execution.operation.ok:
        status = {
            "NOT_FOUND": 404,
            "TICKET_NOT_FOUND": 404,
            "PERMISSION_DENIED": 403,
            "OPERATOR_REQUIRED": 403,
            "VERSION_CONFLICT": 409,
            "IDEMPOTENCY_CONFLICT": 409,
            "VALIDATION_ERROR": 422,
        }.get(execution.operation.code.upper(), 400)
        raise ApiError(status, execution.operation.code.upper(), "工单升级未通过业务校验。")
    return OperationResponse.model_validate(execution.operation)

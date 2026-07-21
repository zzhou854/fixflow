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
from app.application.auth import AuthenticatedIdentity
from app.application.query_models import QueryActor
from app.domain.enums import IssueCategory, Severity, TicketStatus

router = APIRouter(prefix="/api/v1/operator", tags=["operator"])


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
    )
    return OperationResponse.model_validate(result)

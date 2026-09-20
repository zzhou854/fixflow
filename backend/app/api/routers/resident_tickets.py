from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query

from app.api.dependencies import ApiServices, get_services, require_resident
from app.api.errors import ApiError
from app.api.schemas.tickets import (
    AcceptRepairRequest,
    OperationResponse,
    PropertyResponse,
    TicketDetailResponse,
    TicketListItemResponse,
    TicketPageResponse,
)
from app.application.auth import AuthenticatedIdentity
from app.application.models import MutationMetadata, ReviewRepairCommand
from app.application.query_models import QueryActor

router = APIRouter(prefix="/api/v1/resident", tags=["resident"])


@router.get("/properties", response_model=tuple[PropertyResponse, ...])
async def list_properties(
    identity: AuthenticatedIdentity = Depends(require_resident),
    services: ApiServices = Depends(get_services),
) -> tuple[PropertyResponse, ...]:
    items = await services.application.list_resident_properties(
        QueryActor(identity.actor_type, identity.actor_id)
    )
    return tuple(PropertyResponse.model_validate(item) for item in items)


@router.get("/tickets", response_model=TicketPageResponse)
async def list_tickets(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: AuthenticatedIdentity = Depends(require_resident),
    services: ApiServices = Depends(get_services),
) -> TicketPageResponse:
    items = await services.application.list_resident_tickets(
        QueryActor(identity.actor_type, identity.actor_id), limit=limit, offset=offset
    )
    return TicketPageResponse(
        items=tuple(TicketListItemResponse.model_validate(item) for item in items),
        limit=limit,
        offset=offset,
    )


@router.get("/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(
    ticket_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_resident),
    services: ApiServices = Depends(get_services),
) -> TicketDetailResponse:
    detail = await services.application.get_ticket_detail(
        QueryActor(identity.actor_type, identity.actor_id), ticket_id
    )
    if detail is None:
        raise ApiError(404, "NOT_FOUND", "未找到工单。")
    return TicketDetailResponse.model_validate(detail)


@router.post("/tickets/{ticket_id}/accept", response_model=OperationResponse)
async def accept_repair(
    ticket_id: UUID,
    request: AcceptRepairRequest,
    identity: AuthenticatedIdentity = Depends(require_resident),
    services: ApiServices = Depends(get_services),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
) -> OperationResponse:
    result = await services.application.review_repair(
        ReviewRepairCommand(
            metadata=MutationMetadata(
                actor_type=identity.actor_type,
                actor_id=identity.actor_id,
                trace_id=uuid4(),
                idempotency_key=idempotency_key,
                occurred_at=datetime.now(UTC),
            ),
            ticket_id=ticket_id,
            expected_ticket_version=request.expected_ticket_version,
            accepted=True,
            expected_appointment_version=request.expected_appointment_version,
        )
    )
    if not result.ok:
        if result.code == "appointment_not_started":
            raise ApiError(
                409,
                "APPOINTMENT_NOT_STARTED",
                "还没到预约上门时间，暂时不能确认维修完成。",
            )
        raise ApiError(409, result.code.upper(), "当前工单状态已经变化，请刷新后重试。")
    return OperationResponse.model_validate(result)

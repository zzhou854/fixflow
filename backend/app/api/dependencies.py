"""Small explicit FastAPI dependency boundary."""

from dataclasses import dataclass

import structlog
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.agent_runtime.orchestration import AgentOrchestrator
from app.api.errors import ApiError
from app.api.services.agent import AgentApiService
from app.api.services.idempotency import ApiIdempotencyStore
from app.api.services.operator import OperatorActionService
from app.api.services.operator_review import OperatorThreadReviewService
from app.api.services.sse import SSEEventBus
from app.application.auth import AuthenticatedIdentity, AuthService
from app.application.services import FixFlowApplicationService
from app.domain.enums import ActorType


@dataclass(frozen=True, slots=True)
class ApiServices:
    auth: AuthService
    application: FixFlowApplicationService
    orchestrator: AgentOrchestrator
    agent: AgentApiService
    operator_actions: OperatorActionService
    operator_review: OperatorThreadReviewService
    idempotency: ApiIdempotencyStore
    events: SSEEventBus
    runtime_mode: str


_bearer = HTTPBearer(auto_error=False)


def get_services(request: Request) -> ApiServices:
    services = getattr(request.app.state, "services", None)
    if not isinstance(services, ApiServices):
        raise ApiError(503, "SERVICE_UNAVAILABLE", "服务尚未完成初始化。", retryable=True)
    return services


async def get_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    services: ApiServices = Depends(get_services),
) -> AuthenticatedIdentity:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise ApiError(401, "TOKEN_INVALID", "缺少有效的访问令牌。")
    return await services.auth.authenticate(credentials.credentials)


async def require_resident(
    identity: AuthenticatedIdentity = Depends(get_identity),
) -> AuthenticatedIdentity:
    if identity.actor_type is not ActorType.RESIDENT:
        raise ApiError(403, "PERMISSION_DENIED", "该接口仅供住户使用。")
    return identity


async def require_operator(
    identity: AuthenticatedIdentity = Depends(get_identity),
) -> AuthenticatedIdentity:
    if identity.actor_type is not ActorType.OPERATOR:
        raise ApiError(403, "PERMISSION_DENIED", "该接口仅供物业操作员使用。")
    return identity


def configure_logger(request: Request) -> None:
    if not hasattr(request.app.state, "logger"):
        request.app.state.logger = structlog.get_logger("fixflow.api")

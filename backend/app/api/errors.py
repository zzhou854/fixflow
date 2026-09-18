"""Stable, sanitized API error envelope and exception mapping."""

from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.agent_runtime.errors import ThreadIdentityConflict
from app.application.agent_reliability import (
    AgentReliabilityConflict,
    AgentThreadNotFound,
    AgentThreadPermissionDenied,
)
from app.application.auth import AuthenticationError, RegistrationError
from app.application.errors import ApplicationError


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    field_errors: dict[str, str] = Field(default_factory=dict)
    trace_id: UUID
    retryable: bool = False


class ApiError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, *, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable


_APPLICATION_STATUS = {
    "permission_denied": 403,
    "resident_not_authorized": 403,
    "operator_required": 403,
    "ticket_not_found": 404,
    "property_not_found": 404,
    "version_conflict": 409,
    "appointment_time_conflict": 409,
}


def _trace(request: Request) -> UUID:
    value = getattr(request.state, "trace_id", None)
    return value if isinstance(value, UUID) else uuid4()


def _response(
    request: Request,
    status: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    field_errors: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorBody(
        code=code,
        message=message,
        field_errors=field_errors or {},
        trace_id=_trace(request),
        retryable=retryable,
    )
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"))


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _response(request, exc.status_code, exc.code, exc.message, retryable=exc.retryable)

    @app.exception_handler(AuthenticationError)
    async def auth_error(request: Request, exc: AuthenticationError) -> JSONResponse:
        status = (
            401 if exc.code in {"TOKEN_INVALID", "TOKEN_EXPIRED", "INVALID_CREDENTIALS"} else 403
        )
        return _response(request, status, exc.code, "认证失败，请重新登录。")

    @app.exception_handler(RegistrationError)
    async def registration_error(request: Request, exc: RegistrationError) -> JSONResponse:
        if exc.code == "USERNAME_TAKEN":
            return _response(request, 409, exc.code, "这个账号已被使用，请换一个。")
        return _response(request, 400, exc.code, "未找到对应房屋，请检查小区和房号。")

    @app.exception_handler(ThreadIdentityConflict)
    async def thread_error(request: Request, exc: ThreadIdentityConflict) -> JSONResponse:
        return _response(request, 403, "THREAD_IDENTITY_CONFLICT", "无权访问该会话。")

    @app.exception_handler(ApplicationError)
    async def application_error(request: Request, exc: ApplicationError) -> JSONResponse:
        code = getattr(exc, "code", "VALIDATION_ERROR")
        return _response(
            request, _APPLICATION_STATUS.get(code, 400), code.upper(), "请求未通过业务校验。"
        )

    @app.exception_handler(AgentThreadNotFound)
    async def agent_reliability_not_found(
        request: Request, exc: AgentThreadNotFound
    ) -> JSONResponse:
        return _response(request, 404, exc.code, "未找到对应记录。")

    @app.exception_handler(AgentThreadPermissionDenied)
    async def agent_reliability_permission(
        request: Request, exc: AgentThreadPermissionDenied
    ) -> JSONResponse:
        return _response(request, 403, exc.code, "无权执行该操作。")

    @app.exception_handler(AgentReliabilityConflict)
    async def agent_reliability_conflict(
        request: Request, exc: AgentReliabilityConflict
    ) -> JSONResponse:
        return _response(request, 409, exc.code, "记录已更新，请刷新后重试。")

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        fields = {".".join(str(part) for part in item["loc"]): item["msg"] for item in errors}
        if any(item["type"] == "union_tag_invalid" for item in errors):
            return _response(
                request,
                409,
                "INTERRUPT_MISMATCH",
                "恢复类型与当前交互不匹配。",
                field_errors=fields,
            )
        return _response(request, 422, "VALIDATION_ERROR", "请求格式不正确。", field_errors=fields)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        request.app.state.logger.exception("unhandled_api_error", exc_info=exc)
        return _response(request, 500, "INTERNAL_ERROR", "服务暂时无法完成请求。", retryable=True)

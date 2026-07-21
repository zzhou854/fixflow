"""FastAPI application factory for the FixFlow product boundary."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.responses import Response

from app.api.composition import open_api_services
from app.api.dependencies import ApiServices
from app.api.errors import install_error_handlers
from app.api.routers import auth, operator, resident_agent, resident_tickets
from app.config import get_settings


class HealthResponse(BaseModel):
    status: str
    service: str


def create_app(
    services: ApiServices | None = None,
    *,
    cors_origins: str | None = None,
) -> FastAPI:
    settings = get_settings() if services is None else None

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if services is not None:
            application.state.services = services
            yield
            return
        assert settings is not None
        async with open_api_services(settings) as live_services:
            application.state.services = live_services
            yield

    application = FastAPI(title="FixFlow API", version="0.2.0", lifespan=lifespan)
    application.state.logger = structlog.get_logger("fixflow.api")
    if services is not None:
        application.state.services = services
    configured_origins = cors_origins or (
        settings.cors_origins
        if settings is not None
        else "http://127.0.0.1:5173,http://localhost:5173"
    )
    origins = [item.strip() for item in configured_origins.split(",") if item.strip()]
    if not origins or "*" in origins:
        raise ValueError("CORS origins must be explicit")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @application.middleware("http")
    async def request_trace(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.trace_id = uuid4()
        response = await call_next(request)
        response.headers["X-Trace-Id"] = str(request.state.trace_id)
        return response

    @application.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="fixflow-api")

    application.include_router(auth.router)
    application.include_router(resident_agent.router)
    application.include_router(resident_tickets.router)
    application.include_router(operator.router)
    install_error_handlers(application)
    return application

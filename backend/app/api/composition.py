"""Explicit lifecycle composition for the authenticated product API."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent_runtime.composition import open_agent_orchestrator
from app.api.demo_providers import DemoDeterministicEmbeddingProvider, DemoScriptedLLMProvider
from app.api.dependencies import ApiServices
from app.api.services.agent import AgentApiService
from app.api.services.idempotency import ApiIdempotencyStore
from app.api.services.operator import OperatorActionService
from app.api.services.operator_review import OperatorThreadReviewService
from app.api.services.operator_trace import OperatorTraceQueryService
from app.api.services.sse import SSEEventBus
from app.application.auth import AuthService
from app.application.ports import UnitOfWork
from app.application.services import FixFlowApplicationService
from app.config import Settings
from app.infrastructure.database.auth_repository import SqlAlchemyAuthUserRepository
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.trace.runtime import TraceRuntime
from app.trace.sanitizer import TraceSanitizer


@asynccontextmanager
async def open_api_services(settings: Settings) -> AsyncIterator[ApiServices]:
    if settings.runtime_mode != "demo":
        raise ValueError("Task 9 supports only FIXFLOW_RUNTIME_MODE=demo")
    _validate_security_settings(settings)
    assert settings.jwt_secret is not None
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    application = FixFlowApplicationService(uow_factory)
    auth = AuthService(
        SqlAlchemyAuthUserRepository(sessions),
        jwt_secret=settings.jwt_secret.get_secret_value(),
        jwt_algorithm=settings.jwt_algorithm,
        access_token_minutes=settings.jwt_access_token_minutes,
    )
    events = SSEEventBus()
    idempotency = ApiIdempotencyStore()
    trace = TraceRuntime(
        sessions,
        TraceSanitizer(
            max_payload_bytes=settings.trace_max_payload_bytes,
            max_string_length=settings.trace_max_string_length,
        ),
    )
    try:
        async with open_agent_orchestrator(
            settings,
            llm_provider=DemoScriptedLLMProvider(),
            embedding_provider=DemoDeterministicEmbeddingProvider(),
            language_model_name="fixflow-demo-scripted-v1",
        ) as orchestrator:
            operator_review = OperatorThreadReviewService(orchestrator)
            yield ApiServices(
                auth=auth,
                application=application,
                orchestrator=orchestrator,
                agent=AgentApiService(orchestrator, application, events, trace),
                operator_actions=OperatorActionService(application, trace),
                operator_review=operator_review,
                operator_trace=OperatorTraceQueryService(operator_review, trace),
                idempotency=idempotency,
                events=events,
                runtime_mode=settings.runtime_mode,
            )
    finally:
        await idempotency.close()
        await events.close()
        await engine.dispose()


def _validate_security_settings(settings: Settings) -> None:
    if settings.jwt_secret is None:
        raise ValueError("FIXFLOW_JWT_SECRET is required")
    secret = settings.jwt_secret.get_secret_value()
    placeholders = {
        "replace-with-at-least-32-random-characters",
        "replace-with-a-random-secret-of-at-least-32-characters",
        "change-me",
        "development-secret",
        "test-secret",
    }
    if len(secret.encode()) < 32 or secret.casefold() in placeholders:
        raise ValueError("FIXFLOW_JWT_SECRET must be a non-placeholder value of at least 32 bytes")
    if settings.jwt_algorithm != "HS256":
        raise ValueError("Task 9 supports only FIXFLOW_JWT_ALGORITHM=HS256")
    origins = [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
    if not origins or "*" in origins:
        raise ValueError("FIXFLOW_CORS_ORIGINS must contain explicit origins")

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse

from app.api.dependencies import ApiServices, get_services, require_resident
from app.api.errors import ApiError
from app.api.schemas.agent import (
    AgentThreadResponse,
    CreateThreadRequest,
    ResumeRequest,
    SendMessageRequest,
    SSEEvent,
)
from app.application.auth import AuthenticatedIdentity

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


@router.post("/threads", response_model=AgentThreadResponse)
async def create_thread(
    request: CreateThreadRequest,
    identity: AuthenticatedIdentity = Depends(require_resident),
    services: ApiServices = Depends(get_services),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
) -> AgentThreadResponse:
    if request.property_id is None:
        raise ApiError(
            400,
            "PROPERTY_CONTEXT_REQUIRED",
            "请先从已授权房屋列表选择房屋。",
        )
    property_id = request.property_id
    return await services.idempotency.execute(
        caller_id=identity.actor_id,
        scope="agent:create-thread",
        key=idempotency_key,
        payload=request.model_dump(mode="json"),
        operation=lambda: services.agent.create_thread(
            identity,
            property_id=property_id,
            message=request.initial_message,
            reference_time=request.reference_time,
            timezone_name=request.timezone_name,
        ),
    )


@router.post("/threads/{thread_id}/messages", response_model=AgentThreadResponse)
async def send_message(
    thread_id: UUID,
    request: SendMessageRequest,
    identity: AuthenticatedIdentity = Depends(require_resident),
    services: ApiServices = Depends(get_services),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
) -> AgentThreadResponse:
    return await services.idempotency.execute(
        caller_id=identity.actor_id,
        scope=f"agent:message:{thread_id}",
        key=idempotency_key,
        payload=request.model_dump(mode="json"),
        operation=lambda: services.agent.send_message(
            identity,
            thread_id=thread_id,
            message=request.message,
            message_id=request.message_id,
            reference_time=request.reference_time,
            timezone_name=request.timezone_name,
        ),
    )


@router.post("/threads/{thread_id}/resume", response_model=AgentThreadResponse)
async def resume(
    thread_id: UUID,
    request: ResumeRequest,
    identity: AuthenticatedIdentity = Depends(require_resident),
    services: ApiServices = Depends(get_services),
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=128),
) -> AgentThreadResponse:
    return await services.idempotency.execute(
        caller_id=identity.actor_id,
        scope=f"agent:resume:{thread_id}",
        key=idempotency_key,
        payload=request.model_dump(mode="json"),
        operation=lambda: services.agent.resume(identity, thread_id=thread_id, request=request),
    )


@router.get("/threads/{thread_id}", response_model=AgentThreadResponse)
async def get_thread(
    thread_id: UUID,
    identity: AuthenticatedIdentity = Depends(require_resident),
    services: ApiServices = Depends(get_services),
) -> AgentThreadResponse:
    result = await services.agent.get_thread(identity, thread_id=thread_id, trace_id=uuid4())
    if result is None:
        raise ApiError(404, "NOT_FOUND", "未找到会话。")
    return result


def _encode(event: SSEEvent) -> str:
    return f"id: {event.event_id}\nevent: {event.event_type}\ndata: {event.model_dump_json()}\n\n"


@router.get("/threads/{thread_id}/events")
async def stream_events(
    thread_id: UUID,
    raw_request: Request,
    identity: AuthenticatedIdentity = Depends(require_resident),
    services: ApiServices = Depends(get_services),
) -> StreamingResponse:
    state = await services.agent.get_thread(identity, thread_id=thread_id, trace_id=uuid4())
    if state is None:
        # SSE deliberately does not distinguish an unknown thread from a thread
        # owned by another resident before subscriber registration.
        raise ApiError(403, "THREAD_IDENTITY_CONFLICT", "无权订阅该会话。")

    async def events() -> AsyncIterator[str]:
        async with services.events.subscribe(thread_id) as queue:
            while not await raw_request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    event = SSEEvent(
                        event_id=uuid4(),
                        event_type="heartbeat",
                        thread_id=thread_id,
                        trace_id=uuid4(),
                        timestamp=datetime.now(UTC),
                        data={},
                    )
                yield _encode(event)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

"""Unit tests proving MCP handlers only translate and call the Application boundary."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from app.application.errors import AuthorizationFailed
from app.application.models import OperationResult
from app.application.query_models import ResidentPropertyReadModel
from app.domain.enums import ActorType, IssueCategory, Severity

from mcp_server.application_adapter import ApplicationGateway, MCPApplicationAdapter
from mcp_server.schemas.appointments import BookAppointmentRequest
from mcp_server.schemas.common import ResultCode
from mcp_server.schemas.properties import GetResidentPropertyRequest
from mcp_server.schemas.tickets import CreateRepairTicketRequest

NOW = datetime(2030, 1, 1, 8, tzinfo=UTC)


def _gateway(**methods: AsyncMock) -> ApplicationGateway:
    defaults = {
        name: AsyncMock()
        for name in (
            "get_resident_property",
            "find_open_repair_tickets",
            "create_ticket",
            "get_ticket_snapshot",
            "list_available_slots",
            "book_appointment",
            "reschedule_appointment",
            "escalate_ticket",
        )
    }
    defaults.update(methods)
    return cast(ApplicationGateway, SimpleNamespace(**defaults))


def _property_request(*, trace_id: UUID | None = None) -> GetResidentPropertyRequest:
    resident_id = uuid4()
    return GetResidentPropertyRequest(
        actor_type=ActorType.RESIDENT,
        actor_id=resident_id,
        trace_id=trace_id or uuid4(),
        resident_id=resident_id,
        property_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_property_handler_passes_typed_actor_and_preserves_trace() -> None:
    request = _property_request()
    method = AsyncMock(
        return_value=ResidentPropertyReadModel(
            resident_id=request.resident_id,
            property_id=request.property_id,
            community_name="Green Garden",
            building_no="1",
            unit_no="2",
            room_no="301",
            address_text="Green Garden 1-2-301",
        )
    )
    adapter = MCPApplicationAdapter(_gateway(get_resident_property=method))
    response = await adapter.get_resident_property(request)

    assert method.await_args is not None
    query = method.await_args.args[0]
    assert query.actor.actor_id == request.actor_id
    assert query.property_id == request.property_id
    assert response.result_code is ResultCode.FOUND
    assert response.trace_id == request.trace_id


@pytest.mark.asyncio
async def test_create_handler_preserves_idempotency_and_uses_injected_clock() -> None:
    ticket_id = uuid4()
    method = AsyncMock(
        return_value=OperationResult(
            ok=True,
            code="TICKET_CREATED",
            resource_type="repair_ticket",
            resource_id=ticket_id,
            resource_version=1,
            data={"status": "OPEN"},
        )
    )
    adapter = MCPApplicationAdapter(_gateway(create_ticket=method), clock=lambda: NOW)
    resident_id = uuid4()
    request = CreateRepairTicketRequest(
        actor_type=ActorType.RESIDENT,
        actor_id=resident_id,
        trace_id=uuid4(),
        idempotency_key="create-1",
        resident_id=resident_id,
        property_id=uuid4(),
        issue_category=IssueCategory.WATER_LEAK,
        issue_location="kitchen",
        issue_description="pipe leak",
        severity=Severity.MEDIUM,
    )
    response = await adapter.create_repair_ticket(request)

    assert method.await_args is not None
    command = method.await_args.args[0]
    assert command.metadata.idempotency_key == "create-1"
    assert command.metadata.trace_id == request.trace_id
    assert command.metadata.occurred_at == NOW
    assert response.result_code is ResultCode.CREATED
    assert response.data is not None and response.data.resource_id == ticket_id


@pytest.mark.asyncio
async def test_book_handler_preserves_expected_version_and_interval() -> None:
    method = AsyncMock(
        return_value=OperationResult(
            ok=False,
            code="version_conflict",
        )
    )
    adapter = MCPApplicationAdapter(_gateway(book_appointment=method), clock=lambda: NOW)
    start = NOW + timedelta(hours=1)
    request = BookAppointmentRequest(
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        trace_id=uuid4(),
        idempotency_key="book-1",
        ticket_id=uuid4(),
        worker_id=uuid4(),
        scheduled_start=start,
        scheduled_end=start + timedelta(hours=1),
        expected_version=7,
    )
    response = await adapter.book_appointment(request)

    assert method.await_args is not None
    command = method.await_args.args[0]
    assert command.expected_ticket_version == 7
    assert (command.starts_at, command.ends_at) == (
        request.scheduled_start,
        request.scheduled_end,
    )
    assert response.result_code is ResultCode.VERSION_CONFLICT


@pytest.mark.asyncio
async def test_application_permission_error_maps_without_internal_details() -> None:
    method = AsyncMock(side_effect=AuthorizationFailed("resident_not_authorized", table="users"))
    request = _property_request()
    response = await MCPApplicationAdapter(
        _gateway(get_resident_property=method)
    ).get_resident_property(request)

    assert response.result_code is ResultCode.PERMISSION_DENIED
    assert response.error is not None and not response.error.retryable
    assert "users" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_unknown_exception_is_sanitized_and_not_mapped_to_validation() -> None:
    method = AsyncMock(side_effect=RuntimeError("SQL SELECT password_hash stack trace"))
    request = _property_request()
    response = await MCPApplicationAdapter(
        _gateway(get_resident_property=method)
    ).get_resident_property(request)

    serialized = response.model_dump_json()
    assert response.result_code is ResultCode.INTERNAL_ERROR
    assert "SQL" not in serialized and "password_hash" not in serialized


@pytest.mark.asyncio
async def test_time_conflict_mapping_is_stable_and_retryable() -> None:
    method = AsyncMock(return_value=OperationResult(ok=False, code="appointment_time_conflict"))
    adapter = MCPApplicationAdapter(_gateway(book_appointment=method), clock=lambda: NOW)
    request = BookAppointmentRequest(
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        trace_id=uuid4(),
        idempotency_key="book-conflict",
        ticket_id=uuid4(),
        worker_id=uuid4(),
        scheduled_start=NOW,
        scheduled_end=NOW + timedelta(hours=1),
        expected_version=1,
    )
    response = await adapter.book_appointment(request)
    assert response.result_code is ResultCode.TIME_CONFLICT
    assert response.error is not None and response.error.retryable

"""Pydantic and generated MCP schema contract tests."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.enums import ActorType, IssueCategory, Severity
from pydantic import BaseModel, ValidationError

from mcp_server.config import MCPSettings
from mcp_server.schemas.appointments import (
    BookAppointmentRequest,
    ListAvailableSlotsRequest,
    RescheduleAppointmentRequest,
)
from mcp_server.schemas.common import ResultCode, ToolResponse
from mcp_server.schemas.tickets import CreateRepairTicketRequest
from mcp_server.server import create_server


def _read_metadata() -> dict[str, object]:
    return {
        "actor_type": ActorType.RESIDENT,
        "actor_id": uuid4(),
        "trace_id": uuid4(),
    }


def _mutation_metadata() -> dict[str, object]:
    return {**_read_metadata(), "idempotency_key": "request-1"}


@pytest.mark.asyncio
async def test_all_eight_tools_publish_closed_input_and_structured_output_schemas() -> None:
    server = create_server(
        MCPSettings(database_url="postgresql+asyncpg://placeholder:placeholder@localhost/test")
    )
    tools = await server.list_tools()
    assert {tool.name for tool in tools} == {
        "get_resident_property",
        "find_open_repair_tickets",
        "create_repair_ticket",
        "get_ticket_snapshot",
        "list_available_slots",
        "book_appointment",
        "reschedule_appointment",
        "escalate_to_operator",
    }
    for tool in tools:
        assert tool.inputSchema["required"] == ["request"]
        request_ref = tool.inputSchema["properties"]["request"]["$ref"].split("/")[-1]
        assert tool.inputSchema["$defs"][request_ref]["additionalProperties"] is False
        assert tool.outputSchema is not None
        assert {"contract_version", "result_code", "message", "trace_id"} <= set(
            tool.outputSchema["properties"]
        )


def test_create_has_no_fake_expected_version_and_requires_mutation_identity() -> None:
    schema = CreateRepairTicketRequest.model_json_schema()
    assert "expected_version" not in schema["properties"]
    assert {"actor_id", "trace_id", "idempotency_key"} <= set(schema["required"])
    with pytest.raises(ValidationError):
        CreateRepairTicketRequest.model_validate(
            {
                "resident_id": str(uuid4()),
                "property_id": str(uuid4()),
                "issue_category": "WATER_LEAK",
                "issue_location": "kitchen",
                "issue_description": "leak",
                "severity": "MEDIUM",
            }
        )


@pytest.mark.parametrize("model", [BookAppointmentRequest, RescheduleAppointmentRequest])
def test_existing_aggregate_mutations_require_positive_versions(
    model: type[BaseModel],
) -> None:
    assert "expected_version" in model.model_json_schema()["required"]


def test_invalid_enum_uuid_and_extra_json_are_rejected() -> None:
    payload = {
        **_mutation_metadata(),
        "resident_id": uuid4(),
        "property_id": uuid4(),
        "issue_category": "UNSUPPORTED",
        "issue_location": "kitchen",
        "issue_description": "leak",
        "severity": Severity.MEDIUM,
        "arbitrary_json": {},
    }
    with pytest.raises(ValidationError):
        CreateRepairTicketRequest.model_validate(payload)
    payload["issue_category"] = IssueCategory.WATER_LEAK
    payload["actor_id"] = "not-a-uuid"
    with pytest.raises(ValidationError):
        CreateRepairTicketRequest.model_validate(payload)


def test_time_window_timezone_and_order_validation() -> None:
    aware = datetime(2030, 1, 1, 9, tzinfo=UTC)
    base = {
        **_read_metadata(),
        "property_id": uuid4(),
        "issue_category": IssueCategory.WATER_LEAK,
        "requested_duration_minutes": 60,
        "max_results": 20,
    }
    with pytest.raises(ValidationError):
        ListAvailableSlotsRequest(
            **base,
            search_window_start=aware.replace(tzinfo=None),
            search_window_end=aware + timedelta(hours=1),
        )
    with pytest.raises(ValidationError):
        ListAvailableSlotsRequest(
            **base,
            search_window_start=aware,
            search_window_end=aware,
        )


def test_response_envelope_forbids_uncontracted_fields() -> None:
    with pytest.raises(ValidationError):
        ToolResponse.model_validate(
            {
                "result_code": ResultCode.FOUND,
                "message": "found",
                "trace_id": uuid4(),
                "orm_entity": object(),
            }
        )

"""Candidate-slot and appointment MCP tool registration."""

from mcp.server.fastmcp import FastMCP

from mcp_server.application_adapter import MCPApplicationAdapter
from mcp_server.schemas.appointments import (
    AvailableSlotsData,
    BookAppointmentRequest,
    ListAvailableSlotsRequest,
    RescheduleAppointmentRequest,
)
from mcp_server.schemas.common import MutationResultData, ToolResponse


def register_appointment_tools(server: FastMCP, adapter: MCPApplicationAdapter) -> None:
    @server.tool(
        name="list_available_slots",
        description="List deterministic 30-minute-aligned candidate slots.",
        structured_output=True,
    )
    async def list_available_slots(
        request: ListAvailableSlotsRequest,
    ) -> ToolResponse[AvailableSlotsData]:
        return await adapter.list_available_slots(request)

    @server.tool(
        name="book_appointment",
        description="Book one confirmed candidate through the Application Service.",
        structured_output=True,
    )
    async def book_appointment(
        request: BookAppointmentRequest,
    ) -> ToolResponse[MutationResultData]:
        return await adapter.book_appointment(request)

    @server.tool(
        name="reschedule_appointment",
        description="Atomically supersede and replace an appointment.",
        structured_output=True,
    )
    async def reschedule_appointment(
        request: RescheduleAppointmentRequest,
    ) -> ToolResponse[MutationResultData]:
        return await adapter.reschedule_appointment(request)

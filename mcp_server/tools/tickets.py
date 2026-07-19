"""Ticket MCP tool registration."""

from mcp.server.fastmcp import FastMCP

from mcp_server.application_adapter import MCPApplicationAdapter
from mcp_server.schemas.common import MutationResultData, ToolResponse
from mcp_server.schemas.tickets import (
    CreateRepairTicketRequest,
    EscalateToOperatorRequest,
    FindOpenRepairTicketsRequest,
    GetTicketSnapshotRequest,
    OpenRepairTicketsData,
    TicketSnapshotData,
)


def register_ticket_tools(server: FastMCP, adapter: MCPApplicationAdapter) -> None:
    @server.tool(
        name="find_open_repair_tickets",
        description="Return exact structured non-terminal repair-ticket candidates.",
        structured_output=True,
    )
    async def find_open_repair_tickets(
        request: FindOpenRepairTicketsRequest,
    ) -> ToolResponse[OpenRepairTicketsData]:
        return await adapter.find_open_repair_tickets(request)

    @server.tool(
        name="create_repair_ticket",
        description="Create an authorized repair ticket through the Application Service.",
        structured_output=True,
    )
    async def create_repair_ticket(
        request: CreateRepairTicketRequest,
    ) -> ToolResponse[MutationResultData]:
        return await adapter.create_repair_ticket(request)

    @server.tool(
        name="get_ticket_snapshot",
        description="Read the latest authorized PostgreSQL ticket snapshot.",
        structured_output=True,
    )
    async def get_ticket_snapshot(
        request: GetTicketSnapshotRequest,
    ) -> ToolResponse[TicketSnapshotData]:
        return await adapter.get_ticket_snapshot(request)

    @server.tool(
        name="escalate_to_operator",
        description="Escalate a ticket through the deterministic Application Service.",
        structured_output=True,
    )
    async def escalate_to_operator(
        request: EscalateToOperatorRequest,
    ) -> ToolResponse[MutationResultData]:
        return await adapter.escalate_to_operator(request)

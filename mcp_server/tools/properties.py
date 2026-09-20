"""Property MCP tool registration."""

from mcp.server.fastmcp import FastMCP

from mcp_server.application_adapter import MCPApplicationAdapter
from mcp_server.schemas.common import ToolResponse
from mcp_server.schemas.properties import GetResidentPropertyRequest, ResidentPropertyData


def register_property_tools(server: FastMCP, adapter: MCPApplicationAdapter) -> None:
    @server.tool(
        name="get_resident_property",
        description="Read one authorized resident-property relation.",
        structured_output=True,
    )
    async def get_resident_property(
        request: GetResidentPropertyRequest,
    ) -> ToolResponse[ResidentPropertyData]:
        return await adapter.get_resident_property(request)

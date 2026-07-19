"""Real MCP SDK client evidence over the Streamable HTTP transport."""

import asyncio
import socket
from typing import Any
from uuid import uuid4

import pytest
import uvicorn
from app.domain.enums import ActorType, IssueCategory, Severity
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_server.config import MCPSettings
from mcp_server.server import create_server


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _wait_until_started(server: uvicorn.Server) -> None:
    for _ in range(100):
        if server.started:
            return
        if server.should_exit:
            break
        await asyncio.sleep(0.05)
    raise AssertionError("Streamable HTTP test server did not start")


@pytest.mark.asyncio
async def test_real_mcp_client_lists_schemas_and_calls_tools(mcp_env: Any) -> None:
    port = _free_tcp_port()
    fastmcp = create_server(
        MCPSettings(
            host="127.0.0.1",
            port=port,
            database_url=mcp_env.database_url,
        )
    )
    server = uvicorn.Server(
        uvicorn.Config(
            fastmcp.streamable_http_app(),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    server_task = asyncio.create_task(server.serve())
    try:
        await _wait_until_started(server)
        async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as client:
                await client.initialize()
                tools = await client.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "get_resident_property",
                    "find_open_repair_tickets",
                    "create_repair_ticket",
                    "get_ticket_snapshot",
                    "list_available_slots",
                    "book_appointment",
                    "reschedule_appointment",
                    "escalate_to_operator",
                }
                property_tool = next(
                    tool for tool in tools.tools if tool.name == "get_resident_property"
                )
                request_ref = property_tool.inputSchema["properties"]["request"]["$ref"]
                request_schema = property_tool.inputSchema["$defs"][request_ref.split("/")[-1]]
                assert request_schema["additionalProperties"] is False
                trace_id = uuid4()
                property_result = await client.call_tool(
                    "get_resident_property",
                    {
                        "request": {
                            "actor_type": ActorType.RESIDENT,
                            "actor_id": str(mcp_env.resident_id),
                            "trace_id": str(trace_id),
                            "resident_id": str(mcp_env.resident_id),
                            "property_id": str(mcp_env.property_id),
                        }
                    },
                )
                assert property_result.isError is False
                assert property_result.structuredContent is not None
                assert property_result.structuredContent["result_code"] == "FOUND"
                assert property_result.structuredContent["trace_id"] == str(trace_id)

                create_result = await client.call_tool(
                    "create_repair_ticket",
                    {
                        "request": {
                            "actor_type": ActorType.RESIDENT,
                            "actor_id": str(mcp_env.resident_id),
                            "trace_id": str(uuid4()),
                            "idempotency_key": uuid4().hex,
                            "resident_id": str(mcp_env.resident_id),
                            "property_id": str(mcp_env.property_id),
                            "issue_category": IssueCategory.WATER_LEAK,
                            "issue_location": f"transport-{uuid4()}",
                            "issue_description": "transport contract test",
                            "severity": Severity.MEDIUM,
                        }
                    },
                )
                assert create_result.isError is False
                assert create_result.structuredContent is not None
                assert create_result.structuredContent["result_code"] == "CREATED"
                assert create_result.structuredContent["data"]["ticket_status"] == "OPEN"
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
        assert server_task.done()

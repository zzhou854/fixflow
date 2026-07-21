"""Production Agent MCP client over a real Streamable HTTP server."""

import asyncio
import socket
from typing import Any
from uuid import uuid4

import pytest
import uvicorn
from app.agent_runtime.errors import MCPClientError
from app.agent_runtime.mcp.client import StreamableHttpPropertyOperationsClient
from app.domain.enums import ActorType
from app.property_operations.contracts.common import ResultCode
from app.property_operations.contracts.properties import GetResidentPropertyRequest

from mcp_server.config import MCPSettings
from mcp_server.server import create_server


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _wait_started(server: uvicorn.Server) -> None:
    for _ in range(100):
        if server.started:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("MCP server did not start")


@pytest.mark.asyncio
async def test_production_client_discovers_contract_and_parses_result(mcp_env: Any) -> None:
    port = _free_tcp_port()
    fastmcp = create_server(
        MCPSettings(host="127.0.0.1", port=port, database_url=mcp_env.database_url)
    )
    server = uvicorn.Server(
        uvicorn.Config(
            fastmcp.streamable_http_app(),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    task = asyncio.create_task(server.serve())
    try:
        await _wait_started(server)
        async with StreamableHttpPropertyOperationsClient(f"http://127.0.0.1:{port}/mcp") as client:
            response = await client.get_resident_property(
                GetResidentPropertyRequest(
                    actor_type=ActorType.RESIDENT,
                    actor_id=mcp_env.resident_id,
                    trace_id=uuid4(),
                    resident_id=mcp_env.resident_id,
                    property_id=mcp_env.property_id,
                )
            )
            assert response.result_code is ResultCode.FOUND
            assert response.data is not None
            assert response.data.property_id == mcp_env.property_id
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


@pytest.mark.asyncio
async def test_production_client_maps_unavailable_server_to_typed_failure() -> None:
    port = _free_tcp_port()
    with pytest.raises(MCPClientError):
        async with StreamableHttpPropertyOperationsClient(
            f"http://127.0.0.1:{port}/mcp", timeout_seconds=1
        ):
            pass

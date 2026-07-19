"""Composition root and Streamable HTTP entry point for property-operations-mcp."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.application.ports import UnitOfWork
from app.application.services import FixFlowApplicationService
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from mcp.server.fastmcp import FastMCP
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mcp_server.application_adapter import MCPApplicationAdapter
from mcp_server.config import MCPSettings
from mcp_server.tools.appointments import register_appointment_tools
from mcp_server.tools.properties import register_property_tools
from mcp_server.tools.tickets import register_ticket_tools

LOGGER = logging.getLogger(__name__)


def create_server(settings: MCPSettings) -> FastMCP:
    """Build one process-wide engine and the approved Application dependency graph."""

    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    application = FixFlowApplicationService(uow_factory)
    adapter = MCPApplicationAdapter(application)

    @asynccontextmanager
    async def lifespan(_server: FastMCP) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await engine.dispose()

    server = FastMCP(
        name="property-operations-mcp",
        instructions="Deterministic property-repair operations backed by PostgreSQL.",
        host=settings.host,
        port=settings.port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        lifespan=lifespan,
    )
    register_property_tools(server, adapter)
    register_ticket_tools(server, adapter)
    register_appointment_tools(server, adapter)
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = MCPSettings()
    LOGGER.info(
        "starting %s transport=streamable-http host=%s port=%s",
        "property-operations-mcp",
        settings.host,
        settings.port,
    )
    create_server(settings).run(transport="streamable-http")

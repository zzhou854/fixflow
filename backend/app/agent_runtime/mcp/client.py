"""Lifecycle-managed Streamable HTTP client for the eight approved business tools."""

import asyncio
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from time import perf_counter
from types import TracebackType
from typing import Protocol, TypeVar
from uuid import NAMESPACE_URL, uuid4, uuid5

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ValidationError

from app.agent_runtime.errors import (
    MCPClientError,
    MCPContractViolation,
    MCPTimeout,
    MCPToolNotFound,
    MCPUnavailable,
)
from app.agent_runtime.execution_context import current_execution_context
from app.infrastructure.database.models.observability import TraceSource
from app.property_operations.contracts.appointments import (
    AvailableSlotsData,
    BookAppointmentRequest,
    ListAvailableSlotsRequest,
    RescheduleAppointmentRequest,
)
from app.property_operations.contracts.common import (
    CONTRACT_VERSION,
    MutationRequest,
    MutationResultData,
    ResultCode,
    ToolResponse,
)
from app.property_operations.contracts.properties import (
    GetResidentPropertyRequest,
    ResidentPropertyData,
)
from app.property_operations.contracts.tickets import (
    CreateRepairTicketRequest,
    EscalateToOperatorRequest,
    FindOpenRepairTicketsRequest,
    GetTicketSnapshotRequest,
    OpenRepairTicketsData,
    TicketSnapshotData,
)
from app.trace.models import TracePayload

APPROVED_TOOL_NAMES = frozenset(
    {
        "get_resident_property",
        "find_open_repair_tickets",
        "create_repair_ticket",
        "get_ticket_snapshot",
        "list_available_slots",
        "book_appointment",
        "reschedule_appointment",
        "escalate_to_operator",
    }
)

DataT = TypeVar("DataT", bound=BaseModel)


class PropertyOperationsClient(Protocol):
    async def get_resident_property(
        self, request: GetResidentPropertyRequest
    ) -> ToolResponse[ResidentPropertyData]: ...

    async def find_open_repair_tickets(
        self, request: FindOpenRepairTicketsRequest
    ) -> ToolResponse[OpenRepairTicketsData]: ...

    async def create_repair_ticket(
        self, request: CreateRepairTicketRequest
    ) -> ToolResponse[MutationResultData]: ...

    async def get_ticket_snapshot(
        self, request: GetTicketSnapshotRequest
    ) -> ToolResponse[TicketSnapshotData]: ...

    async def list_available_slots(
        self, request: ListAvailableSlotsRequest
    ) -> ToolResponse[AvailableSlotsData]: ...

    async def book_appointment(
        self, request: BookAppointmentRequest
    ) -> ToolResponse[MutationResultData]: ...

    async def reschedule_appointment(
        self, request: RescheduleAppointmentRequest
    ) -> ToolResponse[MutationResultData]: ...

    async def escalate_to_operator(
        self, request: EscalateToOperatorRequest
    ) -> ToolResponse[MutationResultData]: ...


class StreamableHttpPropertyOperationsClient:
    """One initialized MCP session shared by graph nodes for its runtime lifetime."""

    def __init__(self, url: str, *, timeout_seconds: float = 10.0) -> None:
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._tools: frozenset[str] = frozenset()

    async def __aenter__(self) -> "StreamableHttpPropertyOperationsClient":
        stack = AsyncExitStack()
        try:
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(self._url)
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            async with asyncio.timeout(self._timeout_seconds):
                await session.initialize()
                discovered = await session.list_tools()
        except TimeoutError as exc:
            await stack.aclose()
            raise MCPTimeout("MCP initialization timed out") from exc
        except (ConnectionError, OSError) as exc:
            await stack.aclose()
            raise MCPUnavailable("property operations MCP is unavailable") from exc
        names = frozenset(tool.name for tool in discovered.tools)
        if names != APPROVED_TOOL_NAMES:
            await stack.aclose()
            raise MCPContractViolation("MCP tool discovery does not match the approved contract")
        for tool in discovered.tools:
            if tool.name in APPROVED_TOOL_NAMES and "request" not in tool.inputSchema.get(
                "properties", {}
            ):
                await stack.aclose()
                raise MCPContractViolation(f"tool {tool.name} is missing its request schema")
        self._stack = stack
        self._session = session
        self._tools = names
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None
        self._tools = frozenset()

    async def _call[ResponseDataT: BaseModel](
        self,
        tool_name: str,
        request: BaseModel,
        response_model: type[ToolResponse[ResponseDataT]],
    ) -> ToolResponse[ResponseDataT]:
        if self._session is None:
            raise MCPUnavailable("MCP client has not been started")
        if tool_name not in self._tools:
            raise MCPToolNotFound(f"unapproved or unavailable MCP tool: {tool_name}")
        try:
            outbound = request
            context = current_execution_context()
            if isinstance(request, MutationRequest) and context is not None:
                operation_id = uuid5(
                    NAMESPACE_URL,
                    f"fixflow:operation:{request.actor_type.value}:{request.actor_id}:"
                    f"{request.idempotency_key}",
                )
                outbound = request.model_copy(
                    update={
                        "run_id": context.run_id,
                        "thread_id": context.thread_id,
                        "operation_id": operation_id,
                    }
                )
            started = perf_counter()
            await self._trace_call(
                tool_name,
                "mcp_call_prepared",
                TracePayload(tool_name=tool_name),
            )
            async with asyncio.timeout(self._timeout_seconds):
                result = await self._session.call_tool(
                    tool_name,
                    {"request": outbound.model_dump(mode="json", exclude_none=True)},
                )
        except TimeoutError as exc:
            await self._trace_call(
                tool_name,
                "mcp_call_failed",
                TracePayload(tool_name=tool_name, error_code="MCP_TIMEOUT", retryable=True),
            )
            raise MCPTimeout(f"MCP tool {tool_name} timed out") from exc
        except (ConnectionError, OSError) as exc:
            await self._trace_call(
                tool_name,
                "mcp_call_failed",
                TracePayload(tool_name=tool_name, error_code="MCP_UNAVAILABLE", retryable=True),
            )
            raise MCPUnavailable(f"MCP tool {tool_name} is unavailable") from exc
        if result.isError or result.structuredContent is None:
            await self._trace_call(
                tool_name,
                "mcp_call_failed",
                TracePayload(tool_name=tool_name, error_code="MCP_EMPTY_RESULT"),
            )
            raise MCPClientError(f"MCP tool {tool_name} returned no structured result")
        try:
            response = response_model.model_validate(result.structuredContent)
        except ValidationError as exc:
            await self._trace_call(
                tool_name,
                "mcp_call_failed",
                TracePayload(tool_name=tool_name, error_code="MCP_CONTRACT_VIOLATION"),
            )
            raise MCPContractViolation(
                f"MCP tool {tool_name} returned invalid structured content"
            ) from exc
        if response.contract_version != CONTRACT_VERSION:
            raise MCPContractViolation(f"MCP tool {tool_name} contract version mismatch")
        await self._trace_call(
            tool_name,
            "mcp_call_completed",
            TracePayload(
                tool_name=tool_name,
                result_code=response.result_code.value,
                latency_ms=max(0, int((perf_counter() - started) * 1000)),
            ),
        )
        return response

    @staticmethod
    async def _trace_call(tool_name: str, event_type: str, payload: TracePayload) -> None:
        context = current_execution_context()
        if context is None or context.trace is None:
            return
        await context.trace.append_event(
            event_key=context.trace.event_key(
                context.run_id, event_type, f"{tool_name}:{uuid4().hex}"
            ),
            run_id=context.run_id,
            thread_id=context.thread_id,
            trace_id=context.trace_id,
            source=TraceSource.MCP,
            event_type=event_type,
            payload=payload,
            occurred_at=datetime.now(UTC),
        )

    async def get_resident_property(
        self, request: GetResidentPropertyRequest
    ) -> ToolResponse[ResidentPropertyData]:
        return await self._call(
            "get_resident_property", request, ToolResponse[ResidentPropertyData]
        )

    async def find_open_repair_tickets(
        self, request: FindOpenRepairTicketsRequest
    ) -> ToolResponse[OpenRepairTicketsData]:
        return await self._call(
            "find_open_repair_tickets", request, ToolResponse[OpenRepairTicketsData]
        )

    async def create_repair_ticket(
        self, request: CreateRepairTicketRequest
    ) -> ToolResponse[MutationResultData]:
        return await self._call("create_repair_ticket", request, ToolResponse[MutationResultData])

    async def get_ticket_snapshot(
        self, request: GetTicketSnapshotRequest
    ) -> ToolResponse[TicketSnapshotData]:
        return await self._call("get_ticket_snapshot", request, ToolResponse[TicketSnapshotData])

    async def list_available_slots(
        self, request: ListAvailableSlotsRequest
    ) -> ToolResponse[AvailableSlotsData]:
        return await self._call("list_available_slots", request, ToolResponse[AvailableSlotsData])

    async def book_appointment(
        self, request: BookAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        return await self._call("book_appointment", request, ToolResponse[MutationResultData])

    async def reschedule_appointment(
        self, request: RescheduleAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        return await self._call("reschedule_appointment", request, ToolResponse[MutationResultData])

    async def escalate_to_operator(
        self, request: EscalateToOperatorRequest
    ) -> ToolResponse[MutationResultData]:
        return await self._call("escalate_to_operator", request, ToolResponse[MutationResultData])


def response_succeeded(response: ToolResponse[BaseModel]) -> bool:
    return response.result_code in {ResultCode.FOUND, ResultCode.CREATED, ResultCode.UPDATED}

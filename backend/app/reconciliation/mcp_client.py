"""Formal Streamable HTTP outcome-query client used only by reconciliation."""

from contextlib import AsyncExitStack
from types import TracebackType

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import TypeAdapter

from app.property_operations.contracts.common import ToolResponse
from app.property_operations.contracts.reconciliation import (
    GetOperationOutcomeRequest,
    OperationAction,
    OperationOutcomeData,
)
from app.reconciliation.models import ClaimedCase, OperationOutcome, OutcomeStatus


class ReconciliationOutcomeMCPClient:
    def __init__(self, url: str) -> None:
        self._url = url
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> "ReconciliationOutcomeMCPClient":
        stack = AsyncExitStack()
        read, write, _ = await stack.enter_async_context(streamable_http_client(self._url))
        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._stack = stack
        self._session = session
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stack:
            await self._stack.aclose()

    async def get_operation_outcome(self, case: ClaimedCase) -> OperationOutcome:
        if self._session is None:
            raise RuntimeError("outcome MCP client is not started")
        request = GetOperationOutcomeRequest(
            operation_id=case.operation_id,
            action=OperationAction(case.action.value),
            request_fingerprint=case.request_fingerprint,
            actor_type=case.actor_type,
            actor_id=case.actor_id,
            user_id=case.user_id,
            property_id=case.property_id,
            target_entity_type=None,
            target_entity_id=case.target_entity_id,
        )
        raw = await self._session.call_tool(
            "get_operation_outcome", {"request": request.model_dump(mode="json", exclude_none=True)}
        )
        if raw.isError or raw.structuredContent is None:
            raise RuntimeError("operation outcome query failed")
        response = TypeAdapter(ToolResponse[OperationOutcomeData]).validate_python(
            raw.structuredContent
        )
        data = response.data
        if data is None:
            raise RuntimeError("operation outcome response has no data")
        safe = None
        if data.status == "COMMITTED":
            safe = {
                "resource_type": data.resource_type,
                "resource_id": str(data.resource_id),
                "result": data.canonical_result,
            }
        return OperationOutcome(
            status=OutcomeStatus(data.status),
            operation_id=case.operation_id,
            action=case.action,
            safe_result=safe,
            reason_code=getattr(data, "reason_code", None),
        )

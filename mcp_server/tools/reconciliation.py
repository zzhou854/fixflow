"""Trusted read-only reconciliation evidence tool."""

from typing import cast
from uuid import UUID

from app.property_operations.contracts.common import ErrorData, ResultCode, ToolResponse
from app.property_operations.contracts.reconciliation import (
    CommittedOperationOutcome,
    GetOperationOutcomeRequest,
    InconsistentOperationOutcome,
    NotCommittedOperationOutcome,
    OperationAction,
    OperationOutcomeData,
)
from app.reconciliation.evidence import (
    OperationOutcomePermissionDenied,
    SqlAlchemyOperationOutcomeQuery,
)
from app.reconciliation.models import OutcomeStatus
from mcp.server.fastmcp import FastMCP


def register_reconciliation_tool(server: FastMCP, query: SqlAlchemyOperationOutcomeQuery) -> None:
    @server.tool(
        name="get_operation_outcome",
        description="Read authoritative evidence for one trusted UNKNOWN_COMMIT case.",
        structured_output=True,
    )
    async def get_operation_outcome(
        request: GetOperationOutcomeRequest,
    ) -> ToolResponse[OperationOutcomeData]:
        try:
            evidence = await query.get_operation_outcome(request)
        except OperationOutcomePermissionDenied:
            return ToolResponse[OperationOutcomeData](
                result_code=ResultCode.PERMISSION_DENIED,
                message="Operation evidence is not available to this identity.",
                error=ErrorData(
                    code="permission_denied",
                    message="Operation evidence is not available to this identity.",
                    retryable=False,
                ),
                trace_id=request.operation_id,
            )
        action = OperationAction(evidence.action.value)
        data: OperationOutcomeData
        if evidence.status is OutcomeStatus.COMMITTED:
            safe = evidence.safe_result or {}
            data = CommittedOperationOutcome(
                operation_id=request.operation_id,
                action=action,
                resource_type=str(safe["resource_type"]),
                resource_id=UUID(str(safe["resource_id"])),
                canonical_result=cast(dict[str, object], safe.get("result", {})),
            )
        elif evidence.status is OutcomeStatus.NOT_COMMITTED:
            data = NotCommittedOperationOutcome(operation_id=request.operation_id, action=action)
        else:
            data = InconsistentOperationOutcome(
                operation_id=request.operation_id,
                action=action,
                reason_code=evidence.reason_code or "EVIDENCE_CONFLICT",
            )
        return ToolResponse[OperationOutcomeData](
            result_code=ResultCode.FOUND,
            message="Authoritative operation evidence returned.",
            data=data,
            trace_id=request.operation_id,
        )

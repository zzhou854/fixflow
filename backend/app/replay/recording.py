"""Capture validated provider, policy, and MCP results at their formal boundaries."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from uuid import NAMESPACE_URL, UUID, uuid5

from app.agent.models import InterpretationNodeResult, InterpretMessageInput
from app.agent.nodes.interpret_message import InterpretMessageNode
from app.agent_runtime.errors import UnknownCommit
from app.agent_runtime.execution_context import current_execution_context
from app.agent_runtime.mcp.client import PropertyOperationsClient
from app.agent_runtime.mcp.delivery import classify_validated_mutation_result
from app.policy.models import PolicyRetrievalRequest, PolicyRetrievalResult
from app.property_operations.contracts.appointments import (
    AvailableSlotsData,
    BookAppointmentRequest,
    ListAvailableSlotsRequest,
    RescheduleAppointmentRequest,
)
from app.property_operations.contracts.common import MutationResultData, ToolResponse
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
from app.replay.canonical import safe_request_fingerprint
from app.replay.steps import (
    DuplicateLookupResultStep,
    InterpretationResultStep,
    MutationResultStep,
    PolicyResultStep,
    PropertyAuthorizationResultStep,
    SlotLookupResultStep,
    TicketSnapshotResultStep,
)


async def _capture(
    prefix: str,
    payload: object,
    request_fingerprint: str | None,
) -> None:
    context = current_execution_context()
    if context is None or context.replay_capture is None:
        return
    await context.replay_capture.external_result(
        step_key_prefix=prefix,
        payload=payload,  # type: ignore[arg-type]
        request_fingerprint=request_fingerprint,
    )


class RecordingInterpretationNode:
    def __init__(self, inner: InterpretMessageNode) -> None:
        self._inner = inner

    async def __call__(self, request: InterpretMessageInput) -> InterpretationNodeResult:
        result = await self._inner(request)
        content_hash = hashlib.sha256(request.current_user_message.encode("utf-8")).hexdigest()
        await _capture(
            "interpretation",
            InterpretationResultStep(
                provider_type="SCRIPTED",
                schema_version=1,
                result=result,
                input_content_hash=content_hash,
            ),
            content_hash,
        )
        return result


class RecordingPolicyService:
    def __init__(
        self,
        inner: Callable[[PolicyRetrievalRequest], Awaitable[PolicyRetrievalResult]],
    ) -> None:
        self._inner = inner

    async def __call__(self, request: PolicyRetrievalRequest) -> PolicyRetrievalResult:
        result = await self._inner(request)
        await _capture(
            "policy",
            PolicyResultStep(
                query_fingerprint=result.policy_query_fingerprint,
                intent_version=result.intent_version,
                result=result,
            ),
            result.policy_query_fingerprint,
        )
        return result


class RecordingPropertyOperationsClient:
    def __init__(self, inner: PropertyOperationsClient) -> None:
        self._inner = inner

    async def get_resident_property(
        self, request: GetResidentPropertyRequest
    ) -> ToolResponse[ResidentPropertyData]:
        result = await self._inner.get_resident_property(request)
        fingerprint = safe_request_fingerprint(request)
        await _capture(
            "property-authorization",
            PropertyAuthorizationResultStep(result=result),
            fingerprint,
        )
        return result

    async def find_open_repair_tickets(
        self, request: FindOpenRepairTicketsRequest
    ) -> ToolResponse[OpenRepairTicketsData]:
        result = await self._inner.find_open_repair_tickets(request)
        fingerprint = safe_request_fingerprint(request)
        await _capture("duplicate-lookup", DuplicateLookupResultStep(result=result), fingerprint)
        return result

    async def create_repair_ticket(
        self, request: CreateRepairTicketRequest
    ) -> ToolResponse[MutationResultData]:
        return await self._mutation("CREATE_TICKET", request, self._inner.create_repair_ticket)

    async def get_ticket_snapshot(
        self, request: GetTicketSnapshotRequest
    ) -> ToolResponse[TicketSnapshotData]:
        result = await self._inner.get_ticket_snapshot(request)
        fingerprint = safe_request_fingerprint(request)
        await _capture("ticket-snapshot", TicketSnapshotResultStep(result=result), fingerprint)
        return result

    async def list_available_slots(
        self, request: ListAvailableSlotsRequest
    ) -> ToolResponse[AvailableSlotsData]:
        result = await self._inner.list_available_slots(request)
        fingerprint = safe_request_fingerprint(request)
        await _capture("slot-lookup", SlotLookupResultStep(result=result), fingerprint)
        return result

    async def book_appointment(
        self, request: BookAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        return await self._mutation("BOOK_APPOINTMENT", request, self._inner.book_appointment)

    async def reschedule_appointment(
        self, request: RescheduleAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        return await self._mutation(
            "RESCHEDULE_APPOINTMENT", request, self._inner.reschedule_appointment
        )

    async def escalate_to_operator(
        self, request: EscalateToOperatorRequest
    ) -> ToolResponse[MutationResultData]:
        raise RuntimeError("Resident graph cannot execute operator escalation")

    async def _mutation[RequestT](
        self,
        action: str,
        request: RequestT,
        call: Callable[[RequestT], Awaitable[ToolResponse[MutationResultData]]],
    ) -> ToolResponse[MutationResultData]:
        fingerprint = safe_request_fingerprint(request)  # type: ignore[arg-type]
        try:
            result = await call(request)
        except UnknownCommit as exc:
            operation_id = (
                exc.operation_id
                if isinstance(exc.operation_id, UUID)
                else uuid5(NAMESPACE_URL, f"fixflow:unknown:{action}:{fingerprint}")
            )
            await _capture(
                "mcp-mutation",
                MutationResultStep(
                    action=action,
                    operation_id=operation_id,
                    request_fingerprint=fingerprint,
                    delivery_classification="UNKNOWN_COMMIT",
                    result=None,
                    business_error_code="UNKNOWN_COMMIT",
                ),
                fingerprint,
            )
            raise
        operation_id = (
            result.data.operation_id
            if isinstance(result.data, MutationResultData) and result.data.operation_id is not None
            else uuid5(NAMESPACE_URL, f"fixflow:result:{action}:{fingerprint}")
        )
        await _capture(
            "mcp-mutation",
            MutationResultStep(
                action=action,
                operation_id=operation_id,
                request_fingerprint=fingerprint,
                delivery_classification=classify_validated_mutation_result(
                    result.result_code
                ).value,
                result=result,
                business_error_code=result.error.code if result.error else None,
            ),
            fingerprint,
        )
        return result

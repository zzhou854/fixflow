"""Tape-driven dependencies for the formal graph; no live IO is reachable."""

from __future__ import annotations

from app.agent.models import (
    ComposeResponseInput,
    ComposeResponseResult,
    InterpretationNodeResult,
    InterpretMessageInput,
    NodeMetadata,
)
from app.agent_runtime.errors import UnknownCommit
from app.agent_runtime.mcp.client import PropertyOperationsClient
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
from app.replay.enums import ReplayStepKind
from app.replay.steps import (
    DuplicateLookupResultStep,
    InterpretationResultStep,
    MutationResultStep,
    PolicyResultStep,
    PropertyAuthorizationResultStep,
    SlotLookupResultStep,
    TicketSnapshotResultStep,
)
from app.replay.tape import ReplayTapeCursor, ReplayTapeMiss


class LiveMutationForbidden(RuntimeError):
    code = "LIVE_MUTATION_FORBIDDEN"


class MutationGuard:
    """A named fail-closed boundary used by replay-only mutation adapters."""

    @staticmethod
    def reject(action: str) -> None:
        raise LiveMutationForbidden(f"live mutation forbidden during replay: {action}")


class RecordedInterpretationNode:
    def __init__(self, cursor: ReplayTapeCursor, input_content_hash: str) -> None:
        self._cursor = cursor
        self._input_content_hash = input_content_hash

    async def __call__(self, request: InterpretMessageInput) -> InterpretationNodeResult:
        del request
        payload = self._cursor.consume(
            ReplayStepKind.INTERPRETATION_RESULT,
            request_fingerprint=self._input_content_hash,
        )
        if not isinstance(payload, InterpretationResultStep):
            raise ReplayTapeMiss("recorded interpretation step has the wrong schema")
        return payload.result


class RecordedPolicyService:
    def __init__(self, cursor: ReplayTapeCursor) -> None:
        self._cursor = cursor

    async def __call__(self, request: PolicyRetrievalRequest) -> PolicyRetrievalResult:
        del request
        payload = self._cursor.consume(ReplayStepKind.POLICY_RESULT)
        if not isinstance(payload, PolicyResultStep):
            raise ReplayTapeMiss("recorded policy step has the wrong schema")
        return payload.result


class RecordedComposeNode:
    async def __call__(self, request: ComposeResponseInput) -> ComposeResponseResult:
        del request
        return ComposeResponseResult(
            response_text="控制面重放已完成。",
            metadata=NodeMetadata(
                provider="RECORDED",
                model="RECORDED",
                prompt_name="replay-compose",
                prompt_version="1",
            ),
        )


class RecordedPropertyOperationsClient(PropertyOperationsClient):
    def __init__(self, cursor: ReplayTapeCursor) -> None:
        self._cursor = cursor

    async def get_resident_property(
        self, request: GetResidentPropertyRequest
    ) -> ToolResponse[ResidentPropertyData]:
        payload = self._cursor.consume(
            ReplayStepKind.PROPERTY_AUTHORIZATION_RESULT,
            request_fingerprint=safe_request_fingerprint(request),
        )
        if not isinstance(payload, PropertyAuthorizationResultStep):
            raise ReplayTapeMiss("recorded property step has the wrong schema")
        return payload.result

    async def find_open_repair_tickets(
        self, request: FindOpenRepairTicketsRequest
    ) -> ToolResponse[OpenRepairTicketsData]:
        payload = self._cursor.consume(
            ReplayStepKind.DUPLICATE_LOOKUP_RESULT,
            request_fingerprint=safe_request_fingerprint(request),
        )
        if not isinstance(payload, DuplicateLookupResultStep):
            raise ReplayTapeMiss("recorded duplicate step has the wrong schema")
        return payload.result

    async def create_repair_ticket(
        self, request: CreateRepairTicketRequest
    ) -> ToolResponse[MutationResultData]:
        return self._mutation("CREATE_TICKET", request)

    async def get_ticket_snapshot(
        self, request: GetTicketSnapshotRequest
    ) -> ToolResponse[TicketSnapshotData]:
        payload = self._cursor.consume(
            ReplayStepKind.TICKET_SNAPSHOT_RESULT,
            request_fingerprint=safe_request_fingerprint(request),
        )
        if not isinstance(payload, TicketSnapshotResultStep):
            raise ReplayTapeMiss("recorded snapshot step has the wrong schema")
        return payload.result

    async def list_available_slots(
        self, request: ListAvailableSlotsRequest
    ) -> ToolResponse[AvailableSlotsData]:
        payload = self._cursor.consume(
            ReplayStepKind.SLOT_LOOKUP_RESULT,
            request_fingerprint=safe_request_fingerprint(request),
        )
        if not isinstance(payload, SlotLookupResultStep):
            raise ReplayTapeMiss("recorded slot step has the wrong schema")
        return payload.result

    async def book_appointment(
        self, request: BookAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        return self._mutation("BOOK_APPOINTMENT", request)

    async def reschedule_appointment(
        self, request: RescheduleAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        return self._mutation("RESCHEDULE_APPOINTMENT", request)

    async def escalate_to_operator(
        self, request: EscalateToOperatorRequest
    ) -> ToolResponse[MutationResultData]:
        del request
        MutationGuard.reject("ESCALATE_TO_OPERATOR")
        raise AssertionError("unreachable")

    def _mutation(
        self,
        action: str,
        request: CreateRepairTicketRequest | BookAppointmentRequest | RescheduleAppointmentRequest,
    ) -> ToolResponse[MutationResultData]:
        fingerprint = safe_request_fingerprint(request)
        payload = self._cursor.consume(
            ReplayStepKind.MCP_MUTATION_RESULT,
            request_fingerprint=fingerprint,
        )
        if not isinstance(payload, MutationResultStep):
            raise ReplayTapeMiss("recorded mutation step has the wrong schema")
        if payload.action != action:
            raise LiveMutationForbidden("recorded mutation action does not match graph request")
        if payload.delivery_classification == "UNKNOWN_COMMIT":
            raise UnknownCommit(payload.operation_id, action)
        if payload.result is None:
            raise RuntimeError("recorded mutation result is missing")
        return payload.result

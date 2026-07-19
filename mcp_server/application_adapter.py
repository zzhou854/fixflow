"""Thin MCP-to-Application adapter and centralized safe result mapping."""

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from app.application.errors import ApplicationError
from app.application.models import (
    BookAppointmentCommand,
    CreateTicketCommand,
    EscalateTicketCommand,
    MutationMetadata,
    OperationResult,
    RescheduleAppointmentCommand,
)
from app.application.query_models import (
    AvailableSlotReadModel,
    FindOpenRepairTicketsQuery,
    GetResidentPropertyQuery,
    GetTicketSnapshotQuery,
    ListAvailableSlotsQuery,
    OpenRepairTicketReadModel,
    QueryActor,
    ResidentPropertyReadModel,
    TicketSnapshotReadModel,
)

from mcp_server.schemas.appointments import (
    AvailableSlotItem,
    AvailableSlotsData,
    BookAppointmentRequest,
    ListAvailableSlotsRequest,
    RescheduleAppointmentRequest,
)
from mcp_server.schemas.common import (
    ErrorData,
    MutationRequest,
    MutationResultData,
    ReadRequest,
    ResultCode,
    ToolResponse,
)
from mcp_server.schemas.properties import GetResidentPropertyRequest, ResidentPropertyData
from mcp_server.schemas.tickets import (
    ActiveAppointmentData,
    CreateRepairTicketRequest,
    EscalateToOperatorRequest,
    FindOpenRepairTicketsRequest,
    GetTicketSnapshotRequest,
    LatestWorkerEventData,
    OpenRepairTicketItem,
    OpenRepairTicketsData,
    TicketSnapshotData,
)


class ApplicationGateway(Protocol):
    """Exact Application surface used by the eight MCP tools."""

    async def get_resident_property(
        self, query: GetResidentPropertyQuery
    ) -> ResidentPropertyReadModel: ...
    async def find_open_repair_tickets(
        self, query: FindOpenRepairTicketsQuery
    ) -> tuple[OpenRepairTicketReadModel, ...]: ...
    async def create_ticket(self, command: CreateTicketCommand) -> OperationResult: ...
    async def get_ticket_snapshot(
        self, query: GetTicketSnapshotQuery
    ) -> TicketSnapshotReadModel: ...
    async def list_available_slots(
        self, query: ListAvailableSlotsQuery
    ) -> tuple[AvailableSlotReadModel, ...]: ...
    async def book_appointment(self, command: BookAppointmentCommand) -> OperationResult: ...
    async def reschedule_appointment(
        self, command: RescheduleAppointmentCommand
    ) -> OperationResult: ...
    async def escalate_ticket(self, command: EscalateTicketCommand) -> OperationResult: ...


_ERROR_RESULTS: dict[str, tuple[ResultCode, str, bool]] = {
    "property_relation_not_found": (ResultCode.NOT_FOUND, "Property relation not found.", False),
    "property_not_found": (ResultCode.NOT_FOUND, "Property not found.", False),
    "ticket_not_found": (ResultCode.NOT_FOUND, "Repair ticket not found.", False),
    "appointment_not_found": (ResultCode.NOT_FOUND, "Appointment not found.", False),
    "resident_not_authorized": (
        ResultCode.PERMISSION_DENIED,
        "The resident is not authorized for this resource.",
        False,
    ),
    "operator_not_authorized": (
        ResultCode.PERMISSION_DENIED,
        "The operator is not authorized.",
        False,
    ),
    "actor_not_authorized": (
        ResultCode.PERMISSION_DENIED,
        "The actor is not authorized.",
        False,
    ),
    "actor_not_allowed": (ResultCode.PERMISSION_DENIED, "The actor is not allowed.", False),
    "version_conflict": (
        ResultCode.VERSION_CONFLICT,
        "The resource version is stale; reread before retrying.",
        False,
    ),
    "active_appointment_exists": (
        ResultCode.ALREADY_EXISTS,
        "The ticket already has an active appointment.",
        False,
    ),
    "appointment_time_conflict": (
        ResultCode.TIME_CONFLICT,
        "The selected worker time is no longer available.",
        True,
    ),
    "worker_not_eligible": (
        ResultCode.VALIDATION_ERROR,
        "The worker does not satisfy the required skill or availability.",
        False,
    ),
    "idempotency_payload_conflict": (
        ResultCode.IDEMPOTENCY_CONFLICT,
        "The idempotency key was used with different request content.",
        False,
    ),
    "idempotency_request_in_progress": (
        ResultCode.OPERATION_IN_PROGRESS,
        "An operation with this idempotency key is still pending.",
        True,
    ),
    "disallowed_duplicate": (
        ResultCode.ALREADY_EXISTS,
        "An exact structured open-ticket candidate already exists.",
        False,
    ),
    "timezone_required": (
        ResultCode.VALIDATION_ERROR,
        "Timezone-aware timestamps are required.",
        False,
    ),
    "invalid_search_window": (
        ResultCode.VALIDATION_ERROR,
        "The search time window is invalid.",
        False,
    ),
    "invalid_requested_duration": (
        ResultCode.VALIDATION_ERROR,
        "The requested duration is invalid.",
        False,
    ),
    "invalid_max_results": (
        ResultCode.VALIDATION_ERROR,
        "The maximum result count is invalid.",
        False,
    ),
}


class MCPApplicationAdapter:
    def __init__(
        self,
        application: ApplicationGateway,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._application = application
        self._clock = clock

    @staticmethod
    def _actor(request: ReadRequest) -> QueryActor:
        return QueryActor(actor_type=request.actor_type, actor_id=request.actor_id)

    def _metadata(self, request: MutationRequest) -> MutationMetadata:
        return MutationMetadata(
            actor_type=request.actor_type,
            actor_id=request.actor_id,
            trace_id=request.trace_id,
            idempotency_key=request.idempotency_key,
            occurred_at=self._clock(),
        )

    @staticmethod
    def _error(code: str, trace_id: UUID) -> ToolResponse[Any]:
        result, message, retryable = _ERROR_RESULTS.get(
            code,
            (ResultCode.INTERNAL_ERROR, "The operation could not be completed.", False),
        )
        return ToolResponse[MutationResultData](
            result_code=result,
            message=message,
            error=ErrorData(code=code, message=message, retryable=retryable),
            trace_id=trace_id,
        )

    @staticmethod
    def _mutation_result(
        result: OperationResult, trace_id: UUID
    ) -> ToolResponse[MutationResultData]:
        if not result.ok:
            return MCPApplicationAdapter._error(result.code, trace_id)
        if result.resource_id is None or result.resource_type is None:
            return MCPApplicationAdapter._unexpected(trace_id)
        result_code = {
            "TICKET_CREATED": ResultCode.CREATED,
            "APPOINTMENT_BOOKED": ResultCode.CREATED,
            "APPOINTMENT_RESCHEDULED": ResultCode.UPDATED,
            "TICKET_ESCALATED": ResultCode.UPDATED,
        }.get(result.code, ResultCode.UPDATED)
        data = result.data
        return ToolResponse[MutationResultData](
            result_code=result_code,
            message="The operation completed successfully.",
            data=MutationResultData(
                resource_type=result.resource_type,
                resource_id=result.resource_id,
                resource_version=result.resource_version,
                replayed=result.replayed,
                ticket_status=data.get("ticket_status") or data.get("status"),
                ticket_version=data.get("ticket_version"),
                appointment_status=data.get("appointment_status"),
                appointment_version=data.get("appointment_version"),
            ),
            trace_id=trace_id,
        )

    @staticmethod
    def _unexpected(trace_id: UUID) -> ToolResponse[Any]:
        message = "An internal server error occurred."
        return ToolResponse[MutationResultData](
            result_code=ResultCode.INTERNAL_ERROR,
            message=message,
            error=ErrorData(
                code="internal_error", message=message, field_errors={}, retryable=False
            ),
            trace_id=trace_id,
        )

    async def get_resident_property(
        self, request: GetResidentPropertyRequest
    ) -> ToolResponse[ResidentPropertyData]:
        try:
            result = await self._application.get_resident_property(
                GetResidentPropertyQuery(
                    actor=self._actor(request),
                    resident_id=request.resident_id,
                    property_id=request.property_id,
                )
            )
            return ToolResponse[ResidentPropertyData](
                result_code=ResultCode.FOUND,
                message="Authorized resident property found.",
                data=ResidentPropertyData(**asdict(result)),
                trace_id=request.trace_id,
            )
        except ApplicationError as exc:
            return self._error(exc.code, request.trace_id)
        except Exception:
            return self._unexpected(request.trace_id)

    async def find_open_repair_tickets(
        self, request: FindOpenRepairTicketsRequest
    ) -> ToolResponse[OpenRepairTicketsData]:
        try:
            rows = await self._application.find_open_repair_tickets(
                FindOpenRepairTicketsQuery(
                    actor=self._actor(request),
                    resident_id=request.resident_id,
                    property_id=request.property_id,
                    issue_category=request.issue_category,
                    normalized_issue_location=request.normalized_issue_location,
                )
            )
            data = OpenRepairTicketsData(
                tickets=tuple(
                    OpenRepairTicketItem(
                        ticket_id=row.ticket_id,
                        ticket_version=row.ticket_version,
                        resident_id=row.resident_id,
                        property_id=row.property_id,
                        issue_category=row.issue_category,
                        issue_location=row.issue_location,
                        severity=row.severity,
                        ticket_status=row.ticket_status,
                        rework_count=row.rework_count,
                    )
                    for row in rows
                )
            )
            return ToolResponse[OpenRepairTicketsData](
                result_code=ResultCode.FOUND,
                message="Exact structured open-ticket candidates returned.",
                data=data,
                trace_id=request.trace_id,
            )
        except ApplicationError as exc:
            return self._error(exc.code, request.trace_id)
        except Exception:
            return self._unexpected(request.trace_id)

    async def create_repair_ticket(
        self, request: CreateRepairTicketRequest
    ) -> ToolResponse[MutationResultData]:
        try:
            result = await self._application.create_ticket(
                CreateTicketCommand(
                    metadata=self._metadata(request),
                    resident_id=request.resident_id,
                    property_id=request.property_id,
                    issue_category=request.issue_category,
                    issue_location=request.issue_location,
                    issue_description=request.issue_description,
                    severity=request.severity,
                    allow_duplicate=request.allow_duplicate,
                )
            )
            return self._mutation_result(result, request.trace_id)
        except Exception:
            return self._unexpected(request.trace_id)

    async def get_ticket_snapshot(
        self, request: GetTicketSnapshotRequest
    ) -> ToolResponse[TicketSnapshotData]:
        try:
            result = await self._application.get_ticket_snapshot(
                GetTicketSnapshotQuery(actor=self._actor(request), ticket_id=request.ticket_id)
            )
            active = result.active_appointment
            latest = result.latest_worker_event
            return ToolResponse[TicketSnapshotData](
                result_code=ResultCode.FOUND,
                message="Current PostgreSQL ticket snapshot returned.",
                data=TicketSnapshotData(
                    ticket_id=result.ticket_id,
                    ticket_version=result.ticket_version,
                    resident_id=result.resident_id,
                    property_id=result.property_id,
                    issue_category=result.issue_category,
                    issue_location=result.issue_location,
                    severity=result.severity,
                    ticket_status=result.ticket_status,
                    rework_count=result.rework_count,
                    escalated_from_status=result.escalated_from_status,
                    active_appointment=(
                        ActiveAppointmentData(
                            appointment_id=active.appointment_id,
                            worker_id=active.worker_id,
                            purpose=active.purpose.value,
                            appointment_status=active.status.value,
                            scheduled_start=active.scheduled_start,
                            scheduled_end=active.scheduled_end,
                            appointment_version=active.appointment_version,
                        )
                        if active
                        else None
                    ),
                    latest_worker_event=(
                        LatestWorkerEventData(
                            event_id=latest.event_id,
                            event_type=latest.event_type,
                            sequence_no=latest.sequence_no,
                            subject_worker_id=latest.subject_worker_id,
                        )
                        if latest
                        else None
                    ),
                ),
                trace_id=request.trace_id,
            )
        except ApplicationError as exc:
            return self._error(exc.code, request.trace_id)
        except Exception:
            return self._unexpected(request.trace_id)

    async def list_available_slots(
        self, request: ListAvailableSlotsRequest
    ) -> ToolResponse[AvailableSlotsData]:
        try:
            rows = await self._application.list_available_slots(
                ListAvailableSlotsQuery(
                    actor=self._actor(request),
                    property_id=request.property_id,
                    issue_category=request.issue_category,
                    search_window_start=request.search_window_start,
                    search_window_end=request.search_window_end,
                    requested_duration_minutes=request.requested_duration_minutes,
                    max_results=request.max_results,
                )
            )
            return ToolResponse[AvailableSlotsData](
                result_code=ResultCode.FOUND,
                message="Deterministic candidate slots returned; booking is not guaranteed.",
                data=AvailableSlotsData(
                    slots=tuple(AvailableSlotItem(**asdict(row)) for row in rows)
                ),
                trace_id=request.trace_id,
            )
        except ApplicationError as exc:
            return self._error(exc.code, request.trace_id)
        except Exception:
            return self._unexpected(request.trace_id)

    async def book_appointment(
        self, request: BookAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        try:
            result = await self._application.book_appointment(
                BookAppointmentCommand(
                    metadata=self._metadata(request),
                    ticket_id=request.ticket_id,
                    worker_id=request.worker_id,
                    starts_at=request.scheduled_start,
                    ends_at=request.scheduled_end,
                    expected_ticket_version=request.expected_version,
                )
            )
            return self._mutation_result(result, request.trace_id)
        except Exception:
            return self._unexpected(request.trace_id)

    async def reschedule_appointment(
        self, request: RescheduleAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        try:
            result = await self._application.reschedule_appointment(
                RescheduleAppointmentCommand(
                    metadata=self._metadata(request),
                    ticket_id=request.ticket_id,
                    appointment_id=request.appointment_id,
                    worker_id=request.worker_id,
                    starts_at=request.scheduled_start,
                    ends_at=request.scheduled_end,
                    expected_ticket_version=request.expected_version,
                    expected_appointment_version=request.expected_appointment_version,
                )
            )
            return self._mutation_result(result, request.trace_id)
        except Exception:
            return self._unexpected(request.trace_id)

    async def escalate_to_operator(
        self, request: EscalateToOperatorRequest
    ) -> ToolResponse[MutationResultData]:
        try:
            result = await self._application.escalate_ticket(
                EscalateTicketCommand(
                    metadata=self._metadata(request),
                    ticket_id=request.ticket_id,
                    expected_ticket_version=request.expected_version,
                    reason_code=request.reason_code.value,
                    reason_text=request.reason_text,
                    evidence=request.evidence,
                )
            )
            return self._mutation_result(result, request.trace_id)
        except Exception:
            return self._unexpected(request.trace_id)

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.agent.enums import LLMRole
from app.agent.nodes.compose_response import ComposeResponseNode
from app.agent.nodes.interpret_message import InterpretMessageNode
from app.agent_runtime.context import RuntimeDependencies
from app.agent_runtime.errors import MCPUnavailable, ThreadIdentityConflict
from app.agent_runtime.graph import build_agent_graph
from app.agent_runtime.models import (
    AgentCallerContext,
    AgentTurnInput,
    AppointmentSlotSelectionInterrupt,
    DuplicateTicketSelectionInterrupt,
    NeedInformationInterrupt,
    ProvideInformationResume,
    RunStatus,
    SelectAppointmentSlotResume,
    SelectDuplicateTicketResume,
)
from app.agent_runtime.orchestration import AgentOrchestrator
from app.domain.enums import (
    ActorType,
    AppointmentStatus,
    IssueCategory,
    Severity,
    TicketStatus,
    WorkflowStage,
)
from app.policy.enums import EvidenceSufficiency, PolicyTopic
from app.policy.models import EmbeddingProfile, PolicyRetrievalRequest, PolicyRetrievalResult
from app.policy.retrieval import policy_query_fingerprint
from app.property_operations.contracts.appointments import (
    AvailableSlotItem,
    AvailableSlotsData,
    BookAppointmentRequest,
    ListAvailableSlotsRequest,
    RescheduleAppointmentRequest,
)
from app.property_operations.contracts.common import (
    ErrorData,
    MutationResultData,
    ResultCode,
    ToolResponse,
)
from app.property_operations.contracts.properties import (
    GetResidentPropertyRequest,
    ResidentPropertyData,
)
from app.property_operations.contracts.tickets import (
    ActiveAppointmentData,
    CreateRepairTicketRequest,
    EscalateToOperatorRequest,
    FindOpenRepairTicketsRequest,
    GetTicketSnapshotRequest,
    OpenRepairTicketItem,
    OpenRepairTicketsData,
    TicketSnapshotData,
)
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel

from tests.fakes.llm import ScriptedLLMProvider


class FakePropertyOperationsClient:
    def __init__(self, resident_id: UUID, property_id: UUID, *, authorized: bool = True) -> None:
        self.resident_id = resident_id
        self.property_id = property_id
        self.ticket_id = uuid4()
        self.appointment_id = uuid4()
        self.worker_id = uuid4()
        self.booked = False
        self.authorized = authorized
        self.duplicate_tickets: tuple[OpenRepairTicketItem, ...] = ()
        self.calls: list[tuple[str, object]] = []

    def _response[DataT: BaseModel](
        self, data: DataT, trace_id: UUID, code: ResultCode = ResultCode.FOUND
    ) -> ToolResponse[DataT]:
        return ToolResponse[DataT](
            result_code=code,
            message="ok",
            data=data,
            trace_id=trace_id,
        )

    async def get_resident_property(
        self, request: GetResidentPropertyRequest
    ) -> ToolResponse[ResidentPropertyData]:
        self.calls.append(("get_resident_property", request))
        if not self.authorized:
            return ToolResponse[ResidentPropertyData](
                result_code=ResultCode.PERMISSION_DENIED,
                message="denied",
                error=ErrorData(
                    code="resident_not_authorized",
                    message="denied",
                    retryable=False,
                ),
                trace_id=request.trace_id,
            )
        return self._response(
            ResidentPropertyData(
                resident_id=self.resident_id,
                property_id=self.property_id,
                community_name="FixFlow小区",
                building_no="3",
                unit_no="1",
                room_no="101",
                address_text="FixFlow小区3栋1单元101",
            ),
            request.trace_id,
        )

    async def find_open_repair_tickets(
        self, request: FindOpenRepairTicketsRequest
    ) -> ToolResponse[OpenRepairTicketsData]:
        self.calls.append(("find_open_repair_tickets", request))
        return self._response(
            OpenRepairTicketsData(tickets=self.duplicate_tickets), request.trace_id
        )

    async def create_repair_ticket(
        self, request: CreateRepairTicketRequest
    ) -> ToolResponse[MutationResultData]:
        self.calls.append(("create_repair_ticket", request))
        return self._response(
            MutationResultData(
                resource_type="repair_ticket",
                resource_id=self.ticket_id,
                resource_version=1,
                ticket_status=TicketStatus.OPEN,
                ticket_version=1,
            ),
            request.trace_id,
            ResultCode.CREATED,
        )

    async def get_ticket_snapshot(
        self, request: GetTicketSnapshotRequest
    ) -> ToolResponse[TicketSnapshotData]:
        self.calls.append(("get_ticket_snapshot", request))
        active = None
        if self.booked:
            active = ActiveAppointmentData(
                appointment_id=self.appointment_id,
                worker_id=self.worker_id,
                purpose="INITIAL_REPAIR",
                appointment_status=AppointmentStatus.BOOKED,
                scheduled_start=datetime(2026, 7, 21, 13, tzinfo=UTC),
                scheduled_end=datetime(2026, 7, 21, 14, tzinfo=UTC),
                appointment_version=1,
            )
        return self._response(
            TicketSnapshotData(
                ticket_id=self.ticket_id,
                ticket_version=2 if self.booked else 1,
                resident_id=self.resident_id,
                property_id=self.property_id,
                issue_category=IssueCategory.WATER_LEAK,
                issue_location="厨房水槽下",
                severity=Severity.MEDIUM,
                ticket_status=TicketStatus.SCHEDULED if self.booked else TicketStatus.OPEN,
                rework_count=0,
                escalated_from_status=None,
                active_appointment=active,
                latest_worker_event=None,
            ),
            request.trace_id,
        )

    async def list_available_slots(
        self, request: ListAvailableSlotsRequest
    ) -> ToolResponse[AvailableSlotsData]:
        self.calls.append(("list_available_slots", request))
        assert request.requested_duration_minutes == 60
        return self._response(
            AvailableSlotsData(
                slots=(
                    AvailableSlotItem(
                        worker_id=self.worker_id,
                        worker_name="维修员",
                        skill_type="PLUMBING",
                        service_area="FixFlow小区",
                        service_area_matched=True,
                        scheduled_start=datetime(2026, 7, 21, 13, tzinfo=UTC),
                        scheduled_end=datetime(2026, 7, 21, 14, tzinfo=UTC),
                        open_ticket_count=0,
                        rank=1,
                        slot_granularity_minutes=30,
                    ),
                )
            ),
            request.trace_id,
        )

    async def book_appointment(
        self, request: BookAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        self.calls.append(("book_appointment", request))
        self.booked = True
        return self._response(
            MutationResultData(
                resource_type="appointment",
                resource_id=self.appointment_id,
                resource_version=1,
                ticket_status=TicketStatus.SCHEDULED,
                ticket_version=2,
                appointment_status=AppointmentStatus.BOOKED,
                appointment_version=1,
            ),
            request.trace_id,
            ResultCode.CREATED,
        )

    async def reschedule_appointment(
        self, request: RescheduleAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        raise AssertionError("not expected")

    async def escalate_to_operator(
        self, request: EscalateToOperatorRequest
    ) -> ToolResponse[MutationResultData]:
        raise AssertionError("not expected")


class FaultAfterCreateClient(FakePropertyOperationsClient):
    def __init__(self, resident_id: UUID, property_id: UUID) -> None:
        super().__init__(resident_id, property_id)
        self._lost_once = False

    async def create_repair_ticket(
        self, request: CreateRepairTicketRequest
    ) -> ToolResponse[MutationResultData]:
        response = await super().create_repair_ticket(request)
        if not self._lost_once:
            self._lost_once = True
            raise MCPUnavailable("response lost after commit")
        if response.data is not None:
            response.data.replayed = True
        return response


class FaultAfterBookClient(FakePropertyOperationsClient):
    def __init__(self, resident_id: UUID, property_id: UUID) -> None:
        super().__init__(resident_id, property_id)
        self._lost_once = False

    async def book_appointment(
        self, request: BookAppointmentRequest
    ) -> ToolResponse[MutationResultData]:
        response = await super().book_appointment(request)
        if not self._lost_once:
            self._lost_once = True
            raise MCPUnavailable("response lost after appointment commit")
        if response.data is not None:
            response.data.replayed = True
        return response


async def _policy_result(request: PolicyRetrievalRequest) -> PolicyRetrievalResult:
    profile = EmbeddingProfile(provider="test", model="test", dimension=384, profile_version="v1")
    return PolicyRetrievalResult(
        evidence=(),
        conflicts=(),
        sufficiency=EvidenceSufficiency.SUFFICIENT,
        missing_policy_topics=(),
        retrieved_as_of=request.as_of,
        intent_version=request.intent_version,
        issue_category=request.issue_category,
        requested_policy_topics=request.policy_topics,
        embedding_profile=profile,
        policy_query_fingerprint=policy_query_fingerprint(request, profile),
    )


def _turn(resident_id: UUID, property_id: UUID | None) -> AgentTurnInput:
    return AgentTurnInput(
        thread_id=uuid4(),
        trace_id=uuid4(),
        actor_type=ActorType.RESIDENT,
        actor_id=resident_id,
        user_id=resident_id,
        property_id=property_id,
        user_message="厨房水槽下漏水，希望明天下午维修",
        reference_time=datetime(2026, 7, 20, 10, tzinfo=UTC),
        timezone_name="UTC",
    )


def _caller(resident_id: UUID) -> AgentCallerContext:
    return AgentCallerContext(
        actor_type=ActorType.RESIDENT,
        actor_id=resident_id,
        user_id=resident_id,
    )


def _orchestrator(
    mcp: FakePropertyOperationsClient,
    structured: dict[str, object] | tuple[dict[str, object], ...],
    *,
    retrieve_policy: Callable[[PolicyRetrievalRequest], Awaitable[PolicyRetrievalResult]] = (
        _policy_result
    ),
) -> AgentOrchestrator:
    scripts = structured if isinstance(structured, tuple) else (structured,)
    llm = ScriptedLLMProvider(structured=scripts, text=("工单已创建并完成预约。",))
    dependencies = RuntimeDependencies(
        mcp=mcp,
        interpret=InterpretMessageNode(llm, model="scripted"),
        compose=ComposeResponseNode(llm, model="scripted"),
        retrieve_policy=retrieve_policy,
    )
    graph = build_agent_graph(dependencies, checkpointer=InMemorySaver())
    return AgentOrchestrator(graph, mcp)


@pytest.mark.asyncio
async def test_missing_property_fails_before_any_tool_or_model_call() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(mcp, {"utterance_intent": "NEW_REPAIR"})
    result = await orchestrator.start_turn(_turn(resident_id, None))
    assert result.run_status is RunStatus.FAILED_SAFE
    assert result.error_code == "PROPERTY_CONTEXT_REQUIRED"
    assert mcp.calls == []


@pytest.mark.asyncio
async def test_new_repair_interrupt_resume_books_once_with_system_duration() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {
                    "starts_at": "2026-07-21T12:00:00Z",
                    "ends_at": "2026-07-21T18:00:00Z",
                }
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    interrupted = await orchestrator.start_turn(turn)
    assert interrupted.run_status is RunStatus.INTERRUPTED
    assert isinstance(interrupted.interrupt, AppointmentSlotSelectionInterrupt)
    fingerprint = interrupted.interrupt.candidates_fingerprint
    resumed = await orchestrator.resume(
        turn.thread_id,
        _caller(resident_id),
        SelectAppointmentSlotResume(
            kind="SELECT_APPOINTMENT_SLOT",
            intent_version=1,
            candidates_fingerprint=fingerprint,
            rank=1,
            trace_id=uuid4(),
        ),
    )
    assert resumed.run_status is RunStatus.COMPLETED
    assert resumed.active_ticket_id == mcp.ticket_id
    assert resumed.active_appointment_id == mcp.appointment_id
    assert [name for name, _ in mcp.calls].count("create_repair_ticket") == 1
    assert [name for name, _ in mcp.calls].count("book_appointment") == 1
    assert [name for name, _ in mcp.calls].count("get_resident_property") == 2
    state = await orchestrator._stored_state(turn.thread_id)
    assert state is not None
    # Selecting rank=1 is structured control input, not a synthetic "1" chat message.
    assert len([item for item in state.conversation_messages if item.role is LLMRole.USER]) == 1


@pytest.mark.asyncio
async def test_safety_signal_routes_review_without_business_mutations() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "ELECTRICAL",
            "issue_location": "客厅",
            "issue_description_update": "插座冒烟",
            "safety_flags": ["ELECTRICAL_HAZARD"],
        },
    )
    result = await orchestrator.start_turn(_turn(resident_id, property_id))
    assert result.run_status is RunStatus.NEEDS_HUMAN_REVIEW
    assert result.workflow_stage is WorkflowStage.EMERGENCY_REVIEW
    names = [name for name, _ in mcp.calls]
    assert names == ["get_resident_property"]


async def _insufficient_policy(request: PolicyRetrievalRequest) -> PolicyRetrievalResult:
    return (await _policy_result(request)).model_copy(
        update={
            "sufficiency": EvidenceSufficiency.INSUFFICIENT,
            "missing_policy_topics": (PolicyTopic.APPOINTMENT,),
        }
    )


async def _late_policy(request: PolicyRetrievalRequest) -> PolicyRetrievalResult:
    return (await _policy_result(request)).model_copy(update={"intent_version": 2})


@pytest.mark.asyncio
@pytest.mark.parametrize("retrieval", [_insufficient_policy, _late_policy])
async def test_insufficient_or_late_policy_never_reaches_ticket_creation(
    retrieval: Callable[[PolicyRetrievalRequest], Awaitable[PolicyRetrievalResult]],
) -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {"starts_at": "2026-07-21T12:00:00Z", "ends_at": "2026-07-21T18:00:00Z"}
            ],
        },
        retrieve_policy=retrieval,
    )
    result = await orchestrator.start_turn(_turn(resident_id, property_id))
    assert result.run_status in {RunStatus.NEEDS_HUMAN_REVIEW, RunStatus.FAILED_SAFE}
    assert all(name != "create_repair_ticket" for name, _ in mcp.calls)
    assert all(name != "list_available_slots" for name, _ in mcp.calls)


@pytest.mark.asyncio
async def test_single_exact_duplicate_is_adopted_without_creating_a_second_ticket() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    mcp.duplicate_tickets = (
        OpenRepairTicketItem(
            ticket_id=mcp.ticket_id,
            ticket_version=1,
            resident_id=resident_id,
            property_id=property_id,
            issue_category=IssueCategory.WATER_LEAK,
            issue_location="厨房水槽下",
            severity=Severity.MEDIUM,
            ticket_status=TicketStatus.OPEN,
            rework_count=0,
        ),
    )
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {"starts_at": "2026-07-21T12:00:00Z", "ends_at": "2026-07-21T18:00:00Z"}
            ],
        },
    )
    result = await orchestrator.start_turn(_turn(resident_id, property_id))
    assert isinstance(result.interrupt, AppointmentSlotSelectionInterrupt)
    assert result.active_ticket_id == mcp.ticket_id
    assert all(name != "create_repair_ticket" for name, _ in mcp.calls)


@pytest.mark.asyncio
async def test_multiple_exact_duplicates_require_a_bound_selection_interrupt() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    mcp.duplicate_tickets = tuple(
        OpenRepairTicketItem(
            ticket_id=uuid4(),
            ticket_version=1,
            resident_id=resident_id,
            property_id=property_id,
            issue_category=IssueCategory.WATER_LEAK,
            issue_location="厨房水槽下",
            severity=Severity.MEDIUM,
            ticket_status=TicketStatus.OPEN,
            rework_count=0,
        )
        for _ in range(2)
    )
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {"starts_at": "2026-07-21T12:00:00Z", "ends_at": "2026-07-21T18:00:00Z"}
            ],
        },
    )
    result = await orchestrator.start_turn(_turn(resident_id, property_id))
    assert result.run_status is RunStatus.INTERRUPTED
    assert isinstance(result.interrupt, DuplicateTicketSelectionInterrupt)
    assert all(name != "create_repair_ticket" for name, _ in mcp.calls)


@pytest.mark.asyncio
async def test_duplicate_selection_rejects_a_ticket_outside_the_interrupt_candidates() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    mcp.duplicate_tickets = tuple(
        OpenRepairTicketItem(
            ticket_id=uuid4(),
            ticket_version=1,
            resident_id=resident_id,
            property_id=property_id,
            issue_category=IssueCategory.WATER_LEAK,
            issue_location="厨房水槽下",
            severity=Severity.MEDIUM,
            ticket_status=TicketStatus.OPEN,
            rework_count=0,
        )
        for _ in range(2)
    )
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {"starts_at": "2026-07-21T12:00:00Z", "ends_at": "2026-07-21T18:00:00Z"}
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    result = await orchestrator.start_turn(turn)
    assert isinstance(result.interrupt, DuplicateTicketSelectionInterrupt)
    rejected = await orchestrator.resume(
        turn.thread_id,
        _caller(resident_id),
        SelectDuplicateTicketResume(
            kind="SELECT_DUPLICATE_TICKET",
            intent_version=1,
            candidates_fingerprint=result.interrupt.candidates_fingerprint,
            ticket_id=uuid4(),
            trace_id=uuid4(),
        ),
    )
    assert rejected.error_code == "RESUME_CONFLICT"
    assert all(name != "create_repair_ticket" for name, _ in mcp.calls)


@pytest.mark.asyncio
async def test_unauthorized_property_stops_before_business_writes() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id, authorized=False)
    orchestrator = _orchestrator(mcp, {"utterance_intent": "NEW_REPAIR"})
    result = await orchestrator.start_turn(_turn(resident_id, property_id))
    assert result.run_status is RunStatus.NEEDS_HUMAN_REVIEW
    assert result.error_code == "PERMISSION_DENIED"
    assert [name for name, _ in mcp.calls] == ["get_resident_property"]


@pytest.mark.asyncio
async def test_existing_thread_rejects_property_switch() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "ELECTRICAL",
            "issue_location": "客厅",
            "issue_description_update": "插座冒烟",
            "safety_flags": ["ELECTRICAL_HAZARD"],
        },
    )
    turn = _turn(resident_id, property_id)
    await orchestrator.start_turn(turn)
    switched = turn.model_copy(
        update={"trace_id": uuid4(), "property_id": uuid4(), "user_message": "继续"}
    )
    result = await orchestrator.start_turn(switched)
    assert result.run_status is RunStatus.FAILED_SAFE
    assert result.error_code == "THREAD_IDENTITY_CONFLICT"
    assert [name for name, _ in mcp.calls] == ["get_resident_property"]


@pytest.mark.asyncio
async def test_lost_mutation_response_retries_same_logical_idempotency_key() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FaultAfterCreateClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {
                    "starts_at": "2026-07-21T12:00:00Z",
                    "ends_at": "2026-07-21T18:00:00Z",
                }
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    failed = await orchestrator.start_turn(turn)
    assert failed.run_status is RunStatus.FAILED_SAFE
    assert failed.error_code == "MCP_UNAVAILABLE"
    assert failed.assistant_message != "工单已创建并完成预约。"
    retried = await orchestrator.start_turn(
        turn.model_copy(update={"trace_id": uuid4(), "user_message": "重试"})
    )
    assert retried.run_status is RunStatus.INTERRUPTED
    requests = [
        request
        for name, request in mcp.calls
        if name == "create_repair_ticket" and isinstance(request, CreateRepairTicketRequest)
    ]
    assert len(requests) == 2
    assert requests[0].idempotency_key == requests[1].idempotency_key
    assert retried.active_ticket_id == mcp.ticket_id


@pytest.mark.asyncio
async def test_lost_booking_response_replays_checkpointed_pending_operation() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FaultAfterBookClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {"starts_at": "2026-07-21T12:00:00Z", "ends_at": "2026-07-21T18:00:00Z"}
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    interrupted = await orchestrator.start_turn(turn)
    assert isinstance(interrupted.interrupt, AppointmentSlotSelectionInterrupt)
    failed = await orchestrator.resume(
        turn.thread_id,
        _caller(resident_id),
        SelectAppointmentSlotResume(
            kind="SELECT_APPOINTMENT_SLOT",
            intent_version=1,
            candidates_fingerprint=interrupted.interrupt.candidates_fingerprint,
            rank=1,
            trace_id=uuid4(),
        ),
    )
    assert failed.error_code == "MCP_UNAVAILABLE"
    stored = await orchestrator._stored_state(turn.thread_id)
    assert stored is not None and stored.pending_operation is not None
    persisted_key = stored.pending_operation.idempotency_key
    replay = await orchestrator.start_turn(
        turn.model_copy(update={"trace_id": uuid4(), "user_message": "重试预约"})
    )
    requests = [
        request
        for name, request in mcp.calls
        if name == "book_appointment" and isinstance(request, BookAppointmentRequest)
    ]
    assert replay.run_status is RunStatus.COMPLETED
    assert replay.active_appointment_id == mcp.appointment_id
    assert len(requests) == 2
    assert {request.idempotency_key for request in requests} == {persisted_key}


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["fingerprint", "intent_version", "rank"])
async def test_invalid_slot_resume_is_rejected_without_booking(invalid: str) -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {
                    "starts_at": "2026-07-21T12:00:00Z",
                    "ends_at": "2026-07-21T18:00:00Z",
                }
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    interrupted = await orchestrator.start_turn(turn)
    assert isinstance(interrupted.interrupt, AppointmentSlotSelectionInterrupt)
    fingerprint = interrupted.interrupt.candidates_fingerprint
    intent_version = 1
    rank = 1
    if invalid == "fingerprint":
        fingerprint = "0" * 64
    elif invalid == "intent_version":
        intent_version = 2
    else:
        rank = 2
    result = await orchestrator.resume(
        turn.thread_id,
        _caller(resident_id),
        SelectAppointmentSlotResume(
            kind="SELECT_APPOINTMENT_SLOT",
            intent_version=intent_version,
            candidates_fingerprint=fingerprint,
            rank=rank,
            trace_id=uuid4(),
        ),
    )
    assert result.run_status is RunStatus.FAILED_SAFE
    assert result.error_code == "RESUME_CONFLICT"
    assert all(name != "book_appointment" for name, _ in mcp.calls)


@pytest.mark.asyncio
async def test_wrong_interrupt_kind_is_rejected_without_booking() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {"starts_at": "2026-07-21T12:00:00Z", "ends_at": "2026-07-21T18:00:00Z"}
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    interrupted = await orchestrator.start_turn(turn)
    assert isinstance(interrupted.interrupt, AppointmentSlotSelectionInterrupt)
    wrong_kind = await orchestrator.resume(
        turn.thread_id,
        _caller(resident_id),
        ProvideInformationResume(
            kind="PROVIDE_INFORMATION",
            intent_version=1,
            user_message="不应接受",
            trace_id=uuid4(),
            reference_time=datetime(2026, 7, 20, 10, tzinfo=UTC),
            timezone_name="UTC",
        ),
    )
    assert wrong_kind.error_code == "RESUME_CONFLICT"


@pytest.mark.asyncio
async def test_repeat_slot_resume_after_completion_is_rejected() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {"starts_at": "2026-07-21T12:00:00Z", "ends_at": "2026-07-21T18:00:00Z"}
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    interrupted = await orchestrator.start_turn(turn)
    assert isinstance(interrupted.interrupt, AppointmentSlotSelectionInterrupt)
    completed = await orchestrator.resume(
        turn.thread_id,
        _caller(resident_id),
        SelectAppointmentSlotResume(
            kind="SELECT_APPOINTMENT_SLOT",
            intent_version=1,
            candidates_fingerprint=interrupted.interrupt.candidates_fingerprint,
            rank=1,
            trace_id=uuid4(),
        ),
    )
    assert completed.run_status is RunStatus.COMPLETED
    repeated = await orchestrator.resume(
        turn.thread_id,
        _caller(resident_id),
        SelectAppointmentSlotResume(
            kind="SELECT_APPOINTMENT_SLOT",
            intent_version=1,
            candidates_fingerprint=interrupted.interrupt.candidates_fingerprint,
            rank=1,
            trace_id=uuid4(),
        ),
    )
    assert repeated.error_code == "RESUME_CONFLICT"
    assert [name for name, _ in mcp.calls].count("book_appointment") == 1


@pytest.mark.asyncio
async def test_database_change_wins_over_interrupted_slot_checkpoint() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {
                    "starts_at": "2026-07-21T12:00:00Z",
                    "ends_at": "2026-07-21T18:00:00Z",
                }
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    interrupted = await orchestrator.start_turn(turn)
    assert isinstance(interrupted.interrupt, AppointmentSlotSelectionInterrupt)
    mcp.booked = True
    result = await orchestrator.resume(
        turn.thread_id,
        _caller(resident_id),
        SelectAppointmentSlotResume(
            kind="SELECT_APPOINTMENT_SLOT",
            intent_version=1,
            candidates_fingerprint=interrupted.interrupt.candidates_fingerprint,
            rank=1,
            trace_id=uuid4(),
        ),
    )
    assert result.run_status is RunStatus.COMPLETED
    assert all(name != "book_appointment" for name, _ in mcp.calls)


@pytest.mark.asyncio
async def test_missing_information_resume_keeps_intent_version_and_continues() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        (
            {
                "utterance_intent": "NEW_REPAIR",
                "issue_category": "WATER_LEAK",
                "issue_description_update": "水槽下方漏水",
            },
            {
                "utterance_intent": "PROVIDE_INFORMATION",
                "issue_location": "厨房水槽下",
                "user_availability_windows": [
                    {
                        "starts_at": "2026-07-21T12:00:00Z",
                        "ends_at": "2026-07-21T18:00:00Z",
                    }
                ],
            },
        ),
    )
    turn = _turn(resident_id, property_id)
    interrupted = await orchestrator.start_turn(turn)
    assert isinstance(interrupted.interrupt, NeedInformationInterrupt)
    resumed = await orchestrator.resume(
        turn.thread_id,
        _caller(resident_id),
        ProvideInformationResume(
            kind="PROVIDE_INFORMATION",
            intent_version=1,
            user_message="位置在厨房水槽下，明天下午有空",
            trace_id=uuid4(),
            reference_time=datetime(2026, 7, 20, 10, 5, tzinfo=UTC),
            timezone_name="UTC",
        ),
    )
    assert isinstance(resumed.interrupt, AppointmentSlotSelectionInterrupt)
    state = await orchestrator.get_state(turn.thread_id, _caller(resident_id), uuid4())
    assert state is not None
    assert state.intent_version == 1
    assert [name for name, _ in mcp.calls].count("create_repair_ticket") == 1
    full_state = await orchestrator._stored_state(turn.thread_id)
    assert full_state is not None
    assert [
        item.content for item in full_state.conversation_messages if item.role is LLMRole.USER
    ] == [
        turn.user_message,
        "位置在厨房水槽下，明天下午有空",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["actor", "user", "actor_type"])
async def test_non_owner_start_turn_is_rejected_without_state_or_tool_leak(kind: str) -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "ELECTRICAL",
            "issue_location": "客厅",
            "issue_description_update": "插座冒烟",
            "safety_flags": ["ELECTRICAL_HAZARD"],
        },
    )
    turn = _turn(resident_id, property_id)
    await orchestrator.start_turn(turn)
    updates: dict[str, object] = {"trace_id": uuid4(), "user_message": "继续"}
    if kind == "actor":
        updates["actor_id"] = uuid4()
    elif kind == "user":
        updates["user_id"] = uuid4()
    else:
        updates["actor_type"] = ActorType.OPERATOR
    result = await orchestrator.start_turn(turn.model_copy(update=updates))
    assert result.run_status is RunStatus.FAILED_SAFE
    assert result.error_code == "THREAD_IDENTITY_CONFLICT"
    assert result.active_ticket_id is None
    assert result.active_appointment_id is None
    assert result.interrupt is None
    assert [name for name, _ in mcp.calls] == ["get_resident_property"]


@pytest.mark.asyncio
async def test_owner_can_continue_with_new_trace_and_is_reauthorized() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    safety: dict[str, object] = {
        "utterance_intent": "NEW_REPAIR",
        "issue_category": "ELECTRICAL",
        "issue_location": "客厅",
        "issue_description_update": "插座冒烟",
        "safety_flags": ["ELECTRICAL_HAZARD"],
    }
    orchestrator = _orchestrator(mcp, (safety, safety))
    turn = _turn(resident_id, property_id)
    await orchestrator.start_turn(turn)
    continued = await orchestrator.start_turn(
        turn.model_copy(update={"trace_id": uuid4(), "user_message": "仍然有危险"})
    )
    assert continued.run_status is RunStatus.NEEDS_HUMAN_REVIEW
    assert [name for name, _ in mcp.calls] == [
        "get_resident_property",
        "get_resident_property",
    ]


@pytest.mark.asyncio
async def test_non_owner_cannot_resume_or_read_an_interrupted_thread() -> None:
    resident_id, property_id, intruder_id = uuid4(), uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {"starts_at": "2026-07-21T12:00:00Z", "ends_at": "2026-07-21T18:00:00Z"}
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    interrupted = await orchestrator.start_turn(turn)
    assert isinstance(interrupted.interrupt, AppointmentSlotSelectionInterrupt)
    intruder = _caller(intruder_id)
    result = await orchestrator.resume(
        turn.thread_id,
        intruder,
        SelectAppointmentSlotResume(
            kind="SELECT_APPOINTMENT_SLOT",
            intent_version=1,
            candidates_fingerprint=interrupted.interrupt.candidates_fingerprint,
            rank=1,
            trace_id=uuid4(),
        ),
    )
    assert result.error_code == "THREAD_IDENTITY_CONFLICT"
    assert result.interrupt is None
    assert result.active_ticket_id is None
    assert all(name != "book_appointment" for name, _ in mcp.calls)
    with pytest.raises(ThreadIdentityConflict):
        await orchestrator.get_state(turn.thread_id, intruder, uuid4())


@pytest.mark.asyncio
async def test_operator_uses_read_only_review_only_for_ticket_linked_thread() -> None:
    resident_id, property_id, operator_id = uuid4(), uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {"starts_at": "2026-07-21T12:00:00Z", "ends_at": "2026-07-21T18:00:00Z"}
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    await orchestrator.start_turn(turn)
    operator = AgentCallerContext(
        actor_type=ActorType.OPERATOR,
        actor_id=operator_id,
        user_id=operator_id,
    )
    before = await orchestrator._stored_state(turn.thread_id)
    with pytest.raises(ThreadIdentityConflict):
        await orchestrator.get_state(turn.thread_id, operator, uuid4())
    reviewed = await orchestrator.get_operator_state(turn.thread_id, operator, uuid4())
    after = await orchestrator._stored_state(turn.thread_id)
    assert reviewed is not None and reviewed.active_ticket_id == mcp.ticket_id
    assert before == after
    assert [name for name, _ in mcp.calls[-2:]] == [
        "get_resident_property",
        "get_ticket_snapshot",
    ]


@pytest.mark.asyncio
async def test_operator_cannot_review_unlinked_human_review_thread() -> None:
    resident_id, property_id, operator_id = uuid4(), uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "ELECTRICAL",
            "issue_location": "客厅",
            "issue_description_update": "插座冒烟",
            "safety_flags": ["ELECTRICAL_HAZARD"],
        },
    )
    turn = _turn(resident_id, property_id)
    result = await orchestrator.start_turn(turn)
    assert result.active_ticket_id is None
    operator = AgentCallerContext(
        actor_type=ActorType.OPERATOR,
        actor_id=operator_id,
        user_id=operator_id,
    )
    assert await orchestrator.get_operator_state(turn.thread_id, operator, uuid4()) is None


@pytest.mark.asyncio
async def test_revoked_property_blocks_next_turn_and_clears_pending_execution() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    safety: dict[str, object] = {
        "utterance_intent": "NEW_REPAIR",
        "issue_category": "ELECTRICAL",
        "issue_location": "客厅",
        "issue_description_update": "插座冒烟",
        "safety_flags": ["ELECTRICAL_HAZARD"],
    }
    orchestrator = _orchestrator(mcp, (safety, safety))
    turn = _turn(resident_id, property_id)
    await orchestrator.start_turn(turn)
    mcp.authorized = False
    result = await orchestrator.start_turn(
        turn.model_copy(update={"trace_id": uuid4(), "user_message": "继续"})
    )
    assert result.error_code == "PERMISSION_DENIED"
    assert [name for name, _ in mcp.calls] == [
        "get_resident_property",
        "get_resident_property",
    ]
    stored = await orchestrator._stored_state(turn.thread_id)
    assert stored is not None
    assert not stored.property_context_verified
    assert stored.pending_operation is None
    assert stored.user_confirmation is None


@pytest.mark.asyncio
async def test_revoked_property_blocks_resume_before_booking() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    orchestrator = _orchestrator(
        mcp,
        {
            "utterance_intent": "NEW_REPAIR",
            "issue_category": "WATER_LEAK",
            "issue_location": "厨房水槽下",
            "issue_description_update": "厨房水槽下漏水",
            "user_availability_windows": [
                {"starts_at": "2026-07-21T12:00:00Z", "ends_at": "2026-07-21T18:00:00Z"}
            ],
        },
    )
    turn = _turn(resident_id, property_id)
    interrupted = await orchestrator.start_turn(turn)
    assert isinstance(interrupted.interrupt, AppointmentSlotSelectionInterrupt)
    mcp.authorized = False
    result = await orchestrator.resume(
        turn.thread_id,
        _caller(resident_id),
        SelectAppointmentSlotResume(
            kind="SELECT_APPOINTMENT_SLOT",
            intent_version=1,
            candidates_fingerprint=interrupted.interrupt.candidates_fingerprint,
            rank=1,
            trace_id=uuid4(),
        ),
    )
    assert result.error_code == "PERMISSION_DENIED"
    assert all(name != "book_appointment" for name, _ in mcp.calls)


@pytest.mark.asyncio
async def test_message_ids_deduplicate_a_replayed_start_turn() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    safety: dict[str, object] = {
        "utterance_intent": "NEW_REPAIR",
        "issue_category": "ELECTRICAL",
        "issue_location": "客厅",
        "issue_description_update": "插座冒烟",
        "safety_flags": ["ELECTRICAL_HAZARD"],
    }
    orchestrator = _orchestrator(mcp, (safety, safety))
    turn = _turn(resident_id, property_id)
    await orchestrator.start_turn(turn)
    await orchestrator.start_turn(turn)
    state = await orchestrator._stored_state(turn.thread_id)
    assert state is not None
    assert len([item for item in state.conversation_messages if item.role is LLMRole.USER]) == 1

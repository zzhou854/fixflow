import asyncio
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.agent.nodes.compose_response import ComposeResponseNode
from app.agent.nodes.interpret_message import InterpretMessageNode
from app.agent_runtime.context import RuntimeDependencies
from app.agent_runtime.execution_context import bind_execution_context
from app.agent_runtime.graph import build_agent_graph
from app.agent_runtime.orchestration import AgentOrchestrator
from app.domain.enums import (
    ActorType,
    IssueCategory,
    Severity,
    TicketStatus,
    WorkflowStage,
)
from app.infrastructure.database.models.observability import AgentRunTrigger
from app.property_operations.contracts.tickets import OpenRepairTicketItem
from app.replay.canonical import sha256_fingerprint
from app.replay.capture import PersistentReplayCapture, message_hash
from app.replay.engine import ReplayEngine
from app.replay.enums import ReplayBundleStatus, ReplayExecutionStatus
from app.replay.models import (
    OperatorActionReplayInput,
    ReplayBundleView,
    ReplayMessageMetadata,
    ReplaySafeAgentState,
    ThreadCreatedReplayInput,
)
from app.replay.recording import (
    RecordingInterpretationNode,
    RecordingPolicyService,
    RecordingPropertyOperationsClient,
)
from app.replay.repository import ReplayRepository
from app.replay.state import state_fingerprint
from app.replay.steps import (
    FinalStateStep,
    OperatorMutationResultStep,
    ReplayStepPayload,
    ReplayStepRecord,
)
from langgraph.checkpoint.memory import InMemorySaver

from tests.fakes.llm import ScriptedLLMProvider
from tests.unit.agent_runtime.test_graph import (
    FakePropertyOperationsClient,
    _policy_result,
    _turn,
)


class InMemoryReplayRepository:
    def __init__(self, bundle: ReplayBundleView) -> None:
        self.bundle = bundle
        self.steps: list[ReplayStepRecord] = []
        self.invalidated = False
        self.integrity_valid = True

    async def set_start_state(
        self, bundle_id: UUID, state: ReplaySafeAgentState
    ) -> ReplayBundleView:
        assert bundle_id == self.bundle.bundle_id
        self.bundle = self.bundle.model_copy(update={"start_state": state})
        return self.bundle

    async def append_step(
        self,
        bundle_id: UUID,
        *,
        step_key: str,
        payload: ReplayStepPayload,
        request_fingerprint: str | None,
        occurred_at: datetime,
    ) -> ReplayStepRecord:
        del occurred_at
        assert bundle_id == self.bundle.bundle_id
        record = ReplayStepRecord(
            sequence_number=len(self.steps) + 1,
            step_kind=payload.kind,
            step_key=step_key,
            request_fingerprint=request_fingerprint,
            response_schema=type(payload).__name__,
            payload=payload,
            step_checksum=f"{len(self.steps) + 1:064x}",
        )
        self.steps.append(record)
        return record

    async def finalize_bundle(
        self,
        bundle_id: UUID,
        *,
        status: ReplayBundleStatus,
        expected_result: ReplaySafeAgentState | None,
        expected_route_fingerprint: str | None,
        expected_state_fingerprint: str | None,
        capture_error_code: str | None,
        finalized_at: datetime,
    ) -> ReplayBundleView:
        assert bundle_id == self.bundle.bundle_id
        self.bundle = self.bundle.model_copy(
            update={
                "status": status,
                "expected_result": expected_result,
                "expected_route_fingerprint": expected_route_fingerprint,
                "expected_state_fingerprint": expected_state_fingerprint,
                "capture_error_code": capture_error_code,
                "step_count": len(self.steps),
                "bundle_checksum": "f" * 64,
                "finalized_at": finalized_at,
            }
        )
        return self.bundle

    async def mark_incomplete(
        self, bundle_id: UUID, *, error_code: str, occurred_at: datetime
    ) -> ReplayBundleView:
        return await self.finalize_bundle(
            bundle_id,
            status=ReplayBundleStatus.INCOMPLETE,
            expected_result=None,
            expected_route_fingerprint=None,
            expected_state_fingerprint=None,
            capture_error_code=error_code,
            finalized_at=occurred_at,
        )

    async def load_steps(self, bundle_id: UUID) -> tuple[ReplayStepRecord, ...]:
        assert bundle_id == self.bundle.bundle_id
        return tuple(self.steps)

    async def validate_integrity(
        self, bundle: ReplayBundleView, steps: tuple[ReplayStepRecord, ...]
    ) -> bool:
        return self.integrity_valid and bundle == self.bundle and steps == tuple(self.steps)

    async def invalidate_bundle(
        self, bundle_id: UUID, *, error_code: str, occurred_at: datetime
    ) -> ReplayBundleView:
        del error_code, occurred_at
        assert bundle_id == self.bundle.bundle_id
        self.invalidated = True
        self.bundle = self.bundle.model_copy(update={"status": ReplayBundleStatus.INVALID})
        return self.bundle


def _capturing_bundle(
    *, run_id: UUID, thread_id: UUID, trace_id: UUID, property_id: UUID, message: str
) -> ReplayBundleView:
    now = datetime(2026, 7, 20, 10, tzinfo=UTC)
    return ReplayBundleView(
        bundle_id=uuid4(),
        original_run_id=run_id,
        thread_id=thread_id,
        original_trace_id=trace_id,
        trigger_type=AgentRunTrigger.THREAD_CREATED,
        status=ReplayBundleStatus.CAPTURING,
        schema_version=1,
        graph_schema_version=1,
        runtime_revision="test",
        start_state=None,
        input_envelope=ThreadCreatedReplayInput(
            property_id=property_id,
            message=ReplayMessageMetadata(
                message_id=uuid4(),
                content_hash=message_hash(message),
                content_length=len(message),
                language="zh-CN",
                message_role="USER",
            ),
            reference_time=now,
            timezone_name="UTC",
        ),
        expected_result=None,
        expected_route_fingerprint=None,
        expected_state_fingerprint=None,
        step_count=0,
        bundle_checksum=None,
        captured_at=now,
        finalized_at=None,
    )


@pytest.mark.asyncio
async def test_formal_graph_replays_same_need_information_run_ten_times() -> None:
    resident_id, property_id = uuid4(), uuid4()
    mcp = FakePropertyOperationsClient(resident_id, property_id)
    turn = _turn(resident_id, property_id)
    llm = ScriptedLLMProvider(
        structured=(
            {
                "utterance_intent": "NEW_REPAIR",
                "issue_category": "WATER_LEAK",
            },
        ),
        text=("请补充故障位置和具体情况。",),
    )
    recording_mcp = RecordingPropertyOperationsClient(mcp)
    graph = build_agent_graph(
        RuntimeDependencies(
            mcp=recording_mcp,
            interpret=RecordingInterpretationNode(InterpretMessageNode(llm, model="scripted")),
            compose=ComposeResponseNode(llm, model="scripted"),
            retrieve_policy=RecordingPolicyService(_policy_result),
        ),
        checkpointer=InMemorySaver(),
    )
    orchestrator = AgentOrchestrator(graph, recording_mcp)
    run_id = uuid4()
    repository = InMemoryReplayRepository(
        _capturing_bundle(
            run_id=run_id,
            thread_id=turn.thread_id,
            trace_id=turn.trace_id,
            property_id=property_id,
            message=turn.user_message,
        )
    )
    capture = PersistentReplayCapture(cast(ReplayRepository, repository), repository.bundle)
    with bind_execution_context(run_id, turn.thread_id, turn.trace_id, None, capture):
        original_result = await orchestrator.start_turn(turn)
    ready = await capture.finalize(original_result)
    assert ready is not None and ready.status is ReplayBundleStatus.READY

    statuses = []
    fingerprints = []
    for _ in range(10):
        result = await ReplayEngine(cast(ReplayRepository, repository)).execute(
            ready, execution_id=uuid4()
        )
        statuses.append(result.status)
        fingerprints.append((result.route_fingerprint, result.state_fingerprint))
    assert statuses == [ReplayExecutionStatus.PASSED] * 10, result.model_dump_json(indent=2)
    assert len(set(fingerprints)) == 1

    rebuilt = await ReplayEngine(cast(ReplayRepository, repository)).execute(
        ready, execution_id=uuid4()
    )
    assert rebuilt.status is ReplayExecutionStatus.PASSED
    assert (
        rebuilt.route_fingerprint,
        rebuilt.state_fingerprint,
    ) == fingerprints[0]

    concurrent = await asyncio.gather(
        *(
            ReplayEngine(cast(ReplayRepository, repository)).execute(ready, execution_id=uuid4())
            for _ in range(5)
        )
    )
    assert [item.status for item in concurrent] == [ReplayExecutionStatus.PASSED] * 5
    assert {(item.route_fingerprint, item.state_fingerprint) for item in concurrent} == {
        fingerprints[0]
    }


@pytest.mark.asyncio
async def test_engine_fails_safe_on_checksum_and_unsupported_schema() -> None:
    run_id, thread_id, trace_id, property_id = uuid4(), uuid4(), uuid4(), uuid4()
    bundle = _capturing_bundle(
        run_id=run_id,
        thread_id=thread_id,
        trace_id=trace_id,
        property_id=property_id,
        message="test",
    ).model_copy(
        update={
            "status": ReplayBundleStatus.READY,
            "bundle_checksum": "a" * 64,
            "start_state": None,
        }
    )
    repository = InMemoryReplayRepository(bundle)
    repository.bundle = bundle.model_copy(update={"schema_version": 2})
    unsupported = await ReplayEngine(cast(ReplayRepository, repository)).execute(
        repository.bundle, execution_id=uuid4()
    )
    assert unsupported.status is ReplayExecutionStatus.UNSUPPORTED_SCHEMA

    repository.bundle = bundle

    repository.integrity_valid = False
    invalid = await ReplayEngine(cast(ReplayRepository, repository)).execute(
        repository.bundle, execution_id=uuid4()
    )
    assert invalid.status is ReplayExecutionStatus.FAILED_SAFE
    assert repository.invalidated


@pytest.mark.asyncio
async def test_operator_escalation_replays_recorded_plan_without_live_mutation() -> None:
    run_id, trace_id, ticket_id = uuid4(), uuid4(), uuid4()
    safe = ReplaySafeAgentState(
        thread_id=None,
        trace_id=trace_id,
        actor_type=ActorType.OPERATOR,
        actor_id=uuid4(),
        user_id=uuid4(),
        property_context_verified=False,
        workflow_stage=WorkflowStage.HUMAN_REVIEW,
        task_intent="REQUEST_HUMAN",
        utterance_intent="REQUEST_HUMAN",
        intent_version=1,
        safety_review_required=False,
        policy_conflict=False,
        pending_action="NONE",
        active_ticket_id=ticket_id,
    )
    request_fingerprint = "a" * 64
    route_fingerprint = sha256_fingerprint(
        {"nodes": (), "routes": (), "operator_action": "ESCALATE_TO_OPERATOR"}
    )
    final_fingerprint = state_fingerprint(safe)
    now = datetime.now(UTC)
    bundle = ReplayBundleView(
        bundle_id=uuid4(),
        original_run_id=run_id,
        thread_id=None,
        original_trace_id=trace_id,
        trigger_type=AgentRunTrigger.OPERATOR_ACTION,
        status=ReplayBundleStatus.READY,
        schema_version=1,
        graph_schema_version=1,
        runtime_revision="test",
        start_state=safe,
        input_envelope=OperatorActionReplayInput(
            action="ESCALATE_TO_OPERATOR",
            target_entity_id=ticket_id,
            expected_version=1,
            request_fingerprint=request_fingerprint,
        ),
        expected_result=safe,
        expected_route_fingerprint=route_fingerprint,
        expected_state_fingerprint=final_fingerprint,
        step_count=2,
        bundle_checksum="b" * 64,
        captured_at=now,
        finalized_at=now,
    )
    repository = InMemoryReplayRepository(bundle)
    mutation = OperatorMutationResultStep(
        action="ESCALATE_TO_OPERATOR",
        operation_id=uuid4(),
        request_fingerprint=request_fingerprint,
        delivery_classification="COMMITTED",
        resource_id=ticket_id,
        resource_version=2,
    )
    final = FinalStateStep(
        state=safe,
        state_fingerprint=final_fingerprint,
        route_fingerprint=route_fingerprint,
    )
    repository.steps = [
        ReplayStepRecord(
            sequence_number=1,
            step_kind=mutation.kind,
            step_key="operator-mutation:1",
            request_fingerprint=request_fingerprint,
            response_schema=type(mutation).__name__,
            payload=mutation,
            step_checksum="c" * 64,
        ),
        ReplayStepRecord(
            sequence_number=2,
            step_kind=final.kind,
            step_key="final-state",
            response_schema=type(final).__name__,
            payload=final,
            step_checksum="d" * 64,
        ),
    ]
    result = await ReplayEngine(cast(ReplayRepository, repository)).execute(
        bundle, execution_id=uuid4()
    )
    assert result.status is ReplayExecutionStatus.PASSED
    assert result.consumed_steps == result.total_steps == 2


@pytest.mark.asyncio
async def test_formal_graph_replays_duplicate_selection_and_manual_review() -> None:
    resident_id, property_id = uuid4(), uuid4()
    cases: tuple[tuple[dict[str, object], bool], ...] = (
        (
            {
                "utterance_intent": "NEW_REPAIR",
                "issue_category": "WATER_LEAK",
                "issue_location": "kitchen",
                "issue_description_update": "leak",
                "user_availability_windows": [
                    {
                        "starts_at": "2026-07-21T12:00:00Z",
                        "ends_at": "2026-07-21T18:00:00Z",
                    }
                ],
            },
            False,
        ),
        (
            {
                "utterance_intent": "NEW_REPAIR",
                "issue_category": "ELECTRICAL",
                "issue_location": "living room",
                "issue_description_update": "socket smoking",
                "safety_flags": ["ELECTRICAL_HAZARD"],
            },
            True,
        ),
    )
    for structured, safety_case in cases:
        mcp = FakePropertyOperationsClient(resident_id, property_id)
        if not safety_case:
            mcp.duplicate_tickets = tuple(
                OpenRepairTicketItem(
                    ticket_id=uuid4(),
                    ticket_version=1,
                    resident_id=resident_id,
                    property_id=property_id,
                    issue_category=IssueCategory.WATER_LEAK,
                    issue_location="kitchen",
                    severity=Severity.MEDIUM,
                    ticket_status=TicketStatus.OPEN,
                    rework_count=0,
                )
                for _ in range(2)
            )
        turn = _turn(resident_id, property_id)
        llm = ScriptedLLMProvider(structured=(structured,), text=("recorded",))
        recording_mcp = RecordingPropertyOperationsClient(mcp)
        graph = build_agent_graph(
            RuntimeDependencies(
                mcp=recording_mcp,
                interpret=RecordingInterpretationNode(InterpretMessageNode(llm, model="scripted")),
                compose=ComposeResponseNode(llm, model="scripted"),
                retrieve_policy=RecordingPolicyService(_policy_result),
            ),
            checkpointer=InMemorySaver(),
        )
        orchestrator = AgentOrchestrator(graph, recording_mcp)
        run_id = uuid4()
        repository = InMemoryReplayRepository(
            _capturing_bundle(
                run_id=run_id,
                thread_id=turn.thread_id,
                trace_id=turn.trace_id,
                property_id=property_id,
                message=turn.user_message,
            )
        )
        capture = PersistentReplayCapture(cast(ReplayRepository, repository), repository.bundle)
        with bind_execution_context(run_id, turn.thread_id, turn.trace_id, None, capture):
            original = await orchestrator.start_turn(turn)
        ready = await capture.finalize(original)
        assert ready is not None and ready.status is ReplayBundleStatus.READY
        if safety_case:
            assert ready.expected_result is not None
            assert ready.expected_result.workflow_stage is WorkflowStage.EMERGENCY_REVIEW
        else:
            assert ready.expected_result is not None
            assert ready.expected_result.duplicate_candidates_fingerprint is not None
        replayed = await ReplayEngine(cast(ReplayRepository, repository)).execute(
            ready, execution_id=uuid4()
        )
        assert replayed.status is ReplayExecutionStatus.PASSED

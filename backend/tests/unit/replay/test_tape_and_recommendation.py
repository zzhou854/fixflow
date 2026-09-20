from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.agent.enums import AgentIntent, PendingAction
from app.agent_runtime.errors import UnknownCommit
from app.domain.enums import ActorType, AppointmentStatus, TicketStatus, WorkflowStage
from app.infrastructure.database.models.observability import AgentRunTrigger
from app.infrastructure.database.models.reconciliation import ReconciliationStatus
from app.property_operations.contracts.appointments import RescheduleAppointmentRequest
from app.property_operations.contracts.common import MutationResultData, ResultCode, ToolResponse
from app.property_operations.contracts.tickets import CreateRepairTicketRequest
from app.replay.canonical import safe_request_fingerprint, sha256_fingerprint
from app.replay.comparison import ReplayGraphObserver
from app.replay.enums import (
    RecoveryRecommendation,
    ReplayBundleStatus,
    ReplayExecutionStatus,
    ReplayMismatchType,
    ReplayStepKind,
)
from app.replay.models import (
    MessageReplayInput,
    ReplayBundleView,
    ReplayExecutionView,
    ReplayMessageMetadata,
    ReplaySafeAgentState,
)
from app.replay.recommendation import recommend_recovery
from app.replay.recorded import RecordedPropertyOperationsClient
from app.replay.state import state_fingerprint
from app.replay.steps import (
    FinalStateStep,
    MutationResultStep,
    NodeEnteredStep,
    ReplayStepRecord,
)
from app.replay.tape import ReplayTapeCursor, ReplayTapeMiss


def _safe(stage: WorkflowStage = WorkflowStage.DONE) -> ReplaySafeAgentState:
    return ReplaySafeAgentState(
        thread_id=uuid4(),
        trace_id=uuid4(),
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        user_id=uuid4(),
        property_context_verified=False,
        workflow_stage=stage,
        task_intent=AgentIntent.UNKNOWN,
        utterance_intent=AgentIntent.UNKNOWN,
        intent_version=1,
        safety_review_required=False,
        policy_conflict=False,
        pending_action=PendingAction.NONE,
    )


def _bundle(
    status: ReplayBundleStatus = ReplayBundleStatus.READY,
    stage: WorkflowStage = WorkflowStage.DONE,
) -> ReplayBundleView:
    safe = _safe(stage)
    now = datetime.now(UTC)
    return ReplayBundleView(
        bundle_id=uuid4(),
        original_run_id=uuid4(),
        thread_id=safe.thread_id,
        original_trace_id=safe.trace_id,
        trigger_type=AgentRunTrigger.MESSAGE,
        status=status,
        schema_version=1,
        graph_schema_version=1,
        runtime_revision="test",
        start_state=safe,
        input_envelope=MessageReplayInput(
            message=ReplayMessageMetadata(
                message_id=uuid4(),
                content_hash="a" * 64,
                content_length=1,
                language="zh-CN",
                message_role="USER",
            ),
            reference_time=now,
            timezone_name="UTC",
        ),
        expected_result=safe,
        expected_route_fingerprint="b" * 64,
        expected_state_fingerprint="c" * 64,
        step_count=0,
        bundle_checksum="d" * 64,
        capture_error_code=None,
        captured_at=now,
        finalized_at=now,
    )


def _execution(status: ReplayExecutionStatus) -> ReplayExecutionView:
    now = datetime.now(UTC)
    return ReplayExecutionView(
        execution_id=uuid4(),
        bundle_id=uuid4(),
        requested_by_actor_id=uuid4(),
        requested_by_user_id=uuid4(),
        status=status,
        runtime_revision="test",
        graph_schema_version=1,
        started_at=now,
        completed_at=now,
    )


def test_tape_rejects_missing_step() -> None:
    cursor = ReplayTapeCursor(())
    with pytest.raises(ReplayTapeMiss):
        cursor.consume(ReplayStepKind.NODE_ENTERED)
    assert cursor.mismatches[0].mismatch_type is ReplayMismatchType.REPLAY_TAPE_MISS


def test_tape_rejects_wrong_kind_as_unexpected_call() -> None:
    payload = NodeEnteredStep(node_name="interpret", state_fingerprint="a" * 64)
    cursor = ReplayTapeCursor(
        (
            ReplayStepRecord(
                sequence_number=1,
                step_kind=payload.kind,
                step_key="node:1",
                response_schema=type(payload).__name__,
                payload=payload,
                step_checksum="b" * 64,
            ),
        )
    )
    with pytest.raises(ReplayTapeMiss):
        cursor.consume(ReplayStepKind.POLICY_RESULT)
    assert cursor.mismatches[0].mismatch_type is ReplayMismatchType.UNEXPECTED_REPLAY_CALL


def test_tape_reports_request_fingerprint_and_unconsumed_steps() -> None:
    payload = NodeEnteredStep(node_name="interpret", state_fingerprint="a" * 64)
    cursor = ReplayTapeCursor(
        (
            ReplayStepRecord(
                sequence_number=1,
                step_kind=payload.kind,
                step_key="node:1",
                request_fingerprint="b" * 64,
                response_schema=type(payload).__name__,
                payload=payload,
                step_checksum="c" * 64,
            ),
            ReplayStepRecord(
                sequence_number=2,
                step_kind=payload.kind,
                step_key="node:2",
                response_schema=type(payload).__name__,
                payload=payload,
                step_checksum="e" * 64,
            ),
        )
    )
    cursor.consume(ReplayStepKind.NODE_ENTERED, request_fingerprint="d" * 64)
    cursor.finish()
    assert [item.mismatch_type for item in cursor.mismatches] == [
        ReplayMismatchType.REQUEST_FINGERPRINT_MISMATCH,
        ReplayMismatchType.UNCONSUMED_REPLAY_STEP,
    ]


@pytest.mark.asyncio
async def test_recorded_mutations_use_tape_and_reproduce_unknown_commit() -> None:
    actor_id, ticket_id, appointment_id, worker_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    reschedule = RescheduleAppointmentRequest(
        actor_type=ActorType.RESIDENT,
        actor_id=actor_id,
        trace_id=uuid4(),
        idempotency_key="not-part-of-safe-fingerprint",
        ticket_id=ticket_id,
        appointment_id=appointment_id,
        worker_id=worker_id,
        scheduled_start=now,
        scheduled_end=now + timedelta(hours=1),
        expected_version=2,
        expected_appointment_version=1,
    )
    response = ToolResponse[MutationResultData](
        result_code=ResultCode.UPDATED,
        message="recorded",
        data=MutationResultData(
            resource_type="appointment",
            resource_id=uuid4(),
            resource_version=1,
            ticket_status=TicketStatus.SCHEDULED,
            ticket_version=3,
            appointment_status=AppointmentStatus.BOOKED,
            appointment_version=1,
        ),
        trace_id=reschedule.trace_id,
    )
    payload = MutationResultStep(
        action="RESCHEDULE_APPOINTMENT",
        operation_id=uuid4(),
        request_fingerprint=safe_request_fingerprint(reschedule),
        delivery_classification="COMMITTED",
        result=response,
    )
    cursor = ReplayTapeCursor(
        (
            ReplayStepRecord(
                sequence_number=1,
                step_kind=payload.kind,
                step_key="mutation:1",
                request_fingerprint=payload.request_fingerprint,
                response_schema=type(payload).__name__,
                payload=payload,
                step_checksum="a" * 64,
            ),
        )
    )
    assert (
        await RecordedPropertyOperationsClient(cursor).reschedule_appointment(reschedule)
    ) == response
    cursor.finish()
    assert cursor.mismatches == []

    create = CreateRepairTicketRequest(
        actor_type=ActorType.RESIDENT,
        actor_id=actor_id,
        trace_id=uuid4(),
        idempotency_key="unknown-create",
        resident_id=actor_id,
        property_id=uuid4(),
        issue_category="WATER_LEAK",
        issue_location="kitchen",
        issue_description="leak",
        severity="MEDIUM",
    )
    unknown = MutationResultStep(
        action="CREATE_TICKET",
        operation_id=uuid4(),
        request_fingerprint=safe_request_fingerprint(create),
        delivery_classification="UNKNOWN_COMMIT",
        result=None,
        business_error_code="UNKNOWN_COMMIT",
        reconciliation_case_id=uuid4(),
    )
    unknown_cursor = ReplayTapeCursor(
        (
            ReplayStepRecord(
                sequence_number=1,
                step_kind=unknown.kind,
                step_key="mutation:unknown",
                request_fingerprint=unknown.request_fingerprint,
                response_schema=type(unknown).__name__,
                payload=unknown,
                step_checksum="b" * 64,
            ),
        )
    )
    with pytest.raises(UnknownCommit) as caught:
        await RecordedPropertyOperationsClient(unknown_cursor).create_repair_ticket(create)
    assert caught.value.operation_id == unknown.operation_id


def test_comparison_marks_changed_control_state_as_diverged() -> None:
    expected = _safe(WorkflowStage.DONE)
    route_fingerprint = sha256_fingerprint({"nodes": [], "routes": []})
    final = FinalStateStep(
        state=expected,
        state_fingerprint=state_fingerprint(expected),
        route_fingerprint=route_fingerprint,
    )
    cursor = ReplayTapeCursor(
        (
            ReplayStepRecord(
                sequence_number=1,
                step_kind=final.kind,
                step_key="final-state",
                response_schema=type(final).__name__,
                payload=final,
                step_checksum="f" * 64,
            ),
        )
    )
    comparison = ReplayGraphObserver(cursor).finish(
        expected.model_copy(update={"workflow_stage": WorkflowStage.HUMAN_REVIEW})
    )
    assert comparison.status is ReplayExecutionStatus.DIVERGED
    assert comparison.mismatches[0].mismatch_type is ReplayMismatchType.FINAL_STATE_MISMATCH


@pytest.mark.parametrize(
    ("bundle", "execution", "case_status", "expected"),
    [
        (
            _bundle(),
            _execution(ReplayExecutionStatus.PASSED),
            None,
            RecoveryRecommendation.NO_ACTION_REQUIRED,
        ),
        (
            _bundle(stage=WorkflowStage.NEED_INFO),
            _execution(ReplayExecutionStatus.PASSED),
            None,
            RecoveryRecommendation.OWNER_RESUME_REQUIRED,
        ),
        (
            _bundle(),
            None,
            ReconciliationStatus.PENDING,
            RecoveryRecommendation.WAIT_FOR_RECONCILIATION,
        ),
        (
            _bundle(),
            None,
            ReconciliationStatus.RESOLVED_NOT_COMMITTED,
            RecoveryRecommendation.OPERATOR_RECHECK_ALLOWED,
        ),
        (
            _bundle(),
            None,
            ReconciliationStatus.MANUAL_REVIEW,
            RecoveryRecommendation.MANUAL_REVIEW_REQUIRED,
        ),
        (
            _bundle(ReplayBundleStatus.INCOMPLETE),
            None,
            None,
            RecoveryRecommendation.REPLAY_EVIDENCE_INCOMPLETE,
        ),
        (
            _bundle(),
            _execution(ReplayExecutionStatus.DIVERGED),
            None,
            RecoveryRecommendation.REPLAY_DIVERGED_REVIEW_REQUIRED,
        ),
    ],
)
def test_recovery_recommendation_matrix(
    bundle: ReplayBundleView,
    execution: ReplayExecutionView | None,
    case_status: ReconciliationStatus | None,
    expected: RecoveryRecommendation,
) -> None:
    assert recommend_recovery(bundle, execution, reconciliation_status=case_status) is expected

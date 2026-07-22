from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.domain.enums import ActorType
from app.fault_injection import FaultAction, FaultPoint, ScriptedFaultInjector
from app.infrastructure.database.models.reconciliation import (
    ReconciliationAction,
    ReconciliationStatus,
)
from app.reconciliation.models import (
    ClaimedCase,
    CreateCase,
    OperationOutcome,
    OutcomeStatus,
    ReconciliationCaseView,
)
from app.reconciliation.worker import ReconciliationWorker


def claimed(attempts: int = 1) -> ClaimedCase:
    now = datetime.now(UTC)
    return ClaimedCase(
        id=uuid4(),
        operation_id=uuid4(),
        action=ReconciliationAction.BOOK_APPOINTMENT,
        thread_id=uuid4(),
        property_id=uuid4(),
        target_entity_id=uuid4(),
        status=ReconciliationStatus.PROCESSING,
        last_evidence_status=None,
        attempt_count=attempts,
        available_at=now,
        resolution_code=None,
        safe_result=None,
        created_at=now,
        updated_at=now,
        claimed_by="w",
        claim_token=uuid4(),
        original_run_id=uuid4(),
        original_trace_id=uuid4(),
        request_fingerprint="a" * 64,
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        user_id=uuid4(),
    )


class Repo:
    def __init__(self, row: ClaimedCase) -> None:
        self.row = row
        self.resolved: list[OperationOutcome] = []
        self.retried: list[str] = []

    async def create_or_get(self, command: CreateCase) -> ReconciliationCaseView:
        raise AssertionError("worker must not create reconciliation cases")

    async def get(self, case_id: UUID) -> ReconciliationCaseView | None:
        raise AssertionError("worker must not inspect checkpoints")

    async def claim(
        self, worker_id: str, *, now: datetime, lease_seconds: int, limit: int
    ) -> tuple[ClaimedCase, ...]:
        return (self.row,)

    async def resolve(
        self, claim: ClaimedCase, outcome: OperationOutcome, *, now: datetime
    ) -> ReconciliationCaseView:
        self.resolved.append(outcome)
        return ReconciliationCaseView.model_validate(
            claim.model_dump(include=set(ReconciliationCaseView.model_fields))
        )

    async def retry(
        self,
        claim: ClaimedCase,
        *,
        now: datetime,
        available_at: datetime,
        error_code: str,
    ) -> None:
        self.retried.append(error_code)


class Outcomes:
    def __init__(self, error: bool = False) -> None:
        self.error = error

    async def get_operation_outcome(self, case: ClaimedCase) -> OperationOutcome:
        if self.error:
            raise OSError("temporary")
        return OperationOutcome(
            status=OutcomeStatus.NOT_COMMITTED, operation_id=case.operation_id, action=case.action
        )


@pytest.mark.asyncio
async def test_worker_resolves_authoritative_outcome_without_replaying_mutation() -> None:
    row = claimed()
    repo = Repo(row)
    worker = ReconciliationWorker(repo, Outcomes(), worker_id="w")
    assert await worker.run_once(now=datetime.now(UTC)) == 1
    assert repo.resolved[0].status is OutcomeStatus.NOT_COMMITTED


@pytest.mark.asyncio
async def test_worker_moves_exhausted_query_failure_to_manual_review() -> None:
    row = claimed(5)
    repo = Repo(row)
    worker = ReconciliationWorker(repo, Outcomes(True), worker_id="w", max_attempts=5)
    await worker.run_once(now=datetime.now(UTC))
    assert repo.resolved[0].status is OutcomeStatus.INCONSISTENT
    assert repo.resolved[0].reason_code == "EVIDENCE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "point",
    [
        FaultPoint.BEFORE_RECONCILIATION_QUERY,
        FaultPoint.AFTER_RECONCILIATION_QUERY_BEFORE_RESOLUTION,
    ],
)
async def test_reconciliation_query_boundary_fault_schedules_fenced_retry(
    point: FaultPoint,
) -> None:
    row = claimed()
    repo = Repo(row)
    worker = ReconciliationWorker(
        repo,
        Outcomes(),
        worker_id="w",
        fault_injector=ScriptedFaultInjector(
            {(row.operation_id, point, 1): FaultAction.CONTROLLED_EXCEPTION}
        ),
    )
    assert await worker.run_once(now=datetime.now(UTC)) == 1
    assert repo.resolved == []
    assert repo.retried == ["EVIDENCE_QUERY_FAILED"]

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.domain.enums import ActorType
from app.fault_injection import FaultAction, FaultPoint, ScriptedFaultInjector
from app.fault_injection.injector import InjectedFault
from app.infrastructure.database.models.observability import AgentTraceEvent
from app.infrastructure.database.models.reconciliation import (
    ReconciliationAction,
    ReconciliationStatus,
)
from app.reconciliation.models import ClaimedCase, CreateCase, OperationOutcome, OutcomeStatus
from app.reconciliation.ports import StaleReconciliationClaim
from app.reconciliation.repository import SqlAlchemyReconciliationRepository
from app.reconciliation.worker import ReconciliationWorker
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


def command(operation_id: UUID | None = None) -> CreateCase:
    return CreateCase(
        operation_id=operation_id or uuid4(),
        action=ReconciliationAction.CREATE_TICKET,
        thread_id=uuid4(),
        original_run_id=uuid4(),
        original_trace_id=uuid4(),
        operation_idempotency_fingerprint="a" * 64,
        request_fingerprint="b" * 64,
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        user_id=uuid4(),
        property_id=uuid4(),
    )


@pytest.mark.asyncio
async def test_unique_case_claim_fencing_and_atomic_resolution_trace(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlAlchemyReconciliationRepository(sessions)
    item = command()
    first = await repo.create_or_get(item)
    second = await repo.create_or_get(item)
    assert first.id == second.id
    now = datetime.now(UTC)
    (old,) = await repo.claim("worker", now=now, lease_seconds=1, limit=1)
    (fresh,) = await repo.claim("worker", now=now + timedelta(seconds=2), lease_seconds=30, limit=1)
    assert old.claim_token != fresh.claim_token
    with pytest.raises(StaleReconciliationClaim):
        await repo.resolve(
            old,
            OperationOutcome(
                status=OutcomeStatus.COMMITTED,
                operation_id=item.operation_id,
                action=item.action,
                safe_result={
                    "resource_type": "repair_ticket",
                    "resource_id": str(uuid4()),
                    "result": {},
                },
            ),
            now=now,
        )
    resolved = await repo.resolve(
        fresh,
        OperationOutcome(
            status=OutcomeStatus.NOT_COMMITTED, operation_id=item.operation_id, action=item.action
        ),
        now=now + timedelta(seconds=2),
    )
    assert resolved.status is ReconciliationStatus.RESOLVED_NOT_COMMITTED
    assert (
        await repo.claim("later", now=now + timedelta(minutes=1), lease_seconds=30, limit=10) == ()
    )
    await engine.dispose()


class NotCommittedOutcomeQuery:
    async def get_operation_outcome(self, case: ClaimedCase) -> OperationOutcome:
        return OperationOutcome(
            status=OutcomeStatus.NOT_COMMITTED,
            operation_id=case.operation_id,
            action=case.action,
        )


@pytest.mark.asyncio
async def test_resolution_commit_then_ack_loss_is_terminal_and_trace_idempotent(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlAlchemyReconciliationRepository(sessions)
    item = command()
    await repo.create_or_get(item)
    faults = ScriptedFaultInjector(
        {
            (item.operation_id, FaultPoint.AFTER_RESOLUTION_COMMIT_BEFORE_ACK, 1): (
                FaultAction.CONNECTION_RESET
            )
        }
    )
    worker = ReconciliationWorker(
        repo,
        NotCommittedOutcomeQuery(),
        worker_id="ack-loss-worker",
        fault_injector=faults,
    )
    with pytest.raises(InjectedFault):
        await worker.run_once(now=datetime.now(UTC))
    assert await worker.run_once(now=datetime.now(UTC) + timedelta(minutes=1)) == 0
    async with sessions() as session:
        row = await session.scalar(
            select(func.count())
            .select_from(AgentTraceEvent)
            .where(
                AgentTraceEvent.operation_id == item.operation_id,
                AgentTraceEvent.event_type == "reconciliation_resolved_not_committed",
            )
        )
        assert row == 1
    await engine.dispose()

"""PostgreSQL reconciliation repository with leased fencing claims."""

import hashlib
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.models.observability import AgentTraceEvent, TraceSource
from app.infrastructure.database.models.reconciliation import (
    EvidenceStatus,
    OperationReconciliationCase,
    ReconciliationStatus,
)
from app.reconciliation.models import (
    ClaimedCase,
    CreateCase,
    OperationOutcome,
    OutcomeStatus,
    ReconciliationCaseView,
)
from app.reconciliation.ports import StaleReconciliationClaim


class SqlAlchemyReconciliationRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create_or_get(self, command: CreateCase) -> ReconciliationCaseView:
        async with self._sessions() as session, session.begin():
            created = await session.scalar(
                insert(OperationReconciliationCase)
                .values(id=uuid4(), case_key=command.case_key, **command.model_dump())
                .on_conflict_do_nothing(index_elements=["operation_id"])
                .returning(OperationReconciliationCase.id)
            )
            row = await session.scalar(
                select(OperationReconciliationCase).where(
                    OperationReconciliationCase.operation_id == command.operation_id
                )
            )
            if row is None or row.case_key != command.case_key:
                raise ValueError("operation reconciliation identity conflict")
            if created is not None:
                for event_type in ("unknown_commit_detected", "reconciliation_case_created"):
                    session.add(
                        AgentTraceEvent(
                            id=uuid4(),
                            event_key=hashlib.sha256(f"{row.id}|{event_type}".encode()).hexdigest(),
                            run_id=None,
                            thread_id=row.thread_id,
                            trace_id=row.original_trace_id,
                            sequence_number=None,
                            source=TraceSource.RECONCILIATION,
                            event_type=event_type,
                            node_name=None,
                            operation_id=row.operation_id,
                            payload={"action": row.action.value, "status": row.status.value},
                            occurred_at=row.created_at,
                        )
                    )
            return ReconciliationCaseView.model_validate(row)

    async def get(self, case_id: UUID) -> ReconciliationCaseView | None:
        async with self._sessions() as session:
            row = await session.get(OperationReconciliationCase, case_id)
            return ReconciliationCaseView.model_validate(row) if row else None

    async def claim(
        self, worker_id: str, *, now: datetime, lease_seconds: int, limit: int
    ) -> tuple[ClaimedCase, ...]:
        async with self._sessions() as session, session.begin():
            rows = list(
                await session.scalars(
                    select(OperationReconciliationCase)
                    .where(
                        or_(
                            (OperationReconciliationCase.status == ReconciliationStatus.PENDING)
                            & (OperationReconciliationCase.available_at <= now),
                            (OperationReconciliationCase.status == ReconciliationStatus.PROCESSING)
                            & (OperationReconciliationCase.claim_expires_at <= now),
                        )
                    )
                    .order_by(
                        OperationReconciliationCase.available_at,
                        OperationReconciliationCase.created_at,
                    )
                    .with_for_update(skip_locked=True)
                    .limit(limit)
                )
            )
            claimed = []
            for row in rows:
                row.status = ReconciliationStatus.PROCESSING
                row.claimed_by = worker_id
                row.claim_token = uuid4()
                row.claim_expires_at = now + timedelta(seconds=lease_seconds)
                row.attempt_count += 1
                session.add(
                    AgentTraceEvent(
                        id=uuid4(),
                        event_key=hashlib.sha256(
                            f"{row.id}|reconciliation_started|{row.claim_token}".encode()
                        ).hexdigest(),
                        run_id=None,
                        thread_id=row.thread_id,
                        trace_id=row.original_trace_id,
                        sequence_number=None,
                        source=TraceSource.RECONCILIATION,
                        event_type="reconciliation_started",
                        node_name=None,
                        operation_id=row.operation_id,
                        payload={"action": row.action.value, "attempt_count": row.attempt_count},
                        occurred_at=now,
                    )
                )
                claimed.append(ClaimedCase.model_validate(row))
            await session.flush()
            return tuple(claimed)

    async def resolve(
        self, claim: ClaimedCase, outcome: OperationOutcome, *, now: datetime
    ) -> ReconciliationCaseView:
        status = {
            OutcomeStatus.COMMITTED: ReconciliationStatus.RESOLVED_COMMITTED,
            OutcomeStatus.NOT_COMMITTED: ReconciliationStatus.RESOLVED_NOT_COMMITTED,
            OutcomeStatus.INCONSISTENT: ReconciliationStatus.MANUAL_REVIEW,
        }[outcome.status]
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(OperationReconciliationCase)
                .where(
                    OperationReconciliationCase.id == claim.id,
                    OperationReconciliationCase.status == ReconciliationStatus.PROCESSING,
                    OperationReconciliationCase.claimed_by == claim.claimed_by,
                    OperationReconciliationCase.claim_token == claim.claim_token,
                )
                .values(
                    status=status,
                    last_evidence_status=EvidenceStatus(outcome.status.value),
                    resolved_at=now,
                    resolution_code=outcome.reason_code or outcome.status.value,
                    safe_result=outcome.safe_result,
                    claimed_by=None,
                    claim_token=None,
                    claim_expires_at=None,
                    updated_at=now,
                )
                .returning(OperationReconciliationCase)
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise StaleReconciliationClaim()
            event_type = {
                OutcomeStatus.COMMITTED: "reconciliation_resolved_committed",
                OutcomeStatus.NOT_COMMITTED: "reconciliation_resolved_not_committed",
                OutcomeStatus.INCONSISTENT: "reconciliation_manual_review",
            }[outcome.status]
            session.add(
                AgentTraceEvent(
                    id=uuid4(),
                    event_key=hashlib.sha256(f"{claim.id}|{event_type}".encode()).hexdigest(),
                    run_id=None,
                    thread_id=claim.thread_id,
                    trace_id=claim.original_trace_id,
                    sequence_number=None,
                    source=TraceSource.RECONCILIATION,
                    event_type=event_type,
                    node_name=None,
                    operation_id=claim.operation_id,
                    payload={"status": outcome.status.value, "action": outcome.action.value},
                    occurred_at=now,
                )
            )
            return ReconciliationCaseView.model_validate(row)

    async def retry(
        self, claim: ClaimedCase, *, now: datetime, available_at: datetime, error_code: str
    ) -> None:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(OperationReconciliationCase)
                .where(
                    OperationReconciliationCase.id == claim.id,
                    OperationReconciliationCase.status == ReconciliationStatus.PROCESSING,
                    OperationReconciliationCase.claimed_by == claim.claimed_by,
                    OperationReconciliationCase.claim_token == claim.claim_token,
                )
                .values(
                    status=ReconciliationStatus.PENDING,
                    available_at=available_at,
                    claimed_by=None,
                    claim_token=None,
                    claim_expires_at=None,
                    last_error_code=error_code,
                    last_error_at=now,
                    updated_at=now,
                )
            )
            if getattr(result, "rowcount", 0) != 1:
                raise StaleReconciliationClaim()
            session.add(
                AgentTraceEvent(
                    id=uuid4(),
                    event_key=hashlib.sha256(
                        f"{claim.id}|reconciliation_retry_scheduled|{claim.claim_token}".encode()
                    ).hexdigest(),
                    run_id=None,
                    thread_id=claim.thread_id,
                    trace_id=claim.original_trace_id,
                    sequence_number=None,
                    source=TraceSource.RECONCILIATION,
                    event_type="reconciliation_retry_scheduled",
                    node_name=None,
                    operation_id=claim.operation_id,
                    payload={"action": claim.action.value, "error_code": error_code},
                    occurred_at=now,
                )
            )

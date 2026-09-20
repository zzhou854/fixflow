"""One bounded reconciliation worker iteration; it never replays mutations."""

from datetime import datetime, timedelta

from app.fault_injection import FaultInjector, FaultPoint, NoOpFaultInjector
from app.reconciliation.models import OperationOutcome, OutcomeStatus
from app.reconciliation.ports import OperationOutcomeQuery, ReconciliationRepository


class ReconciliationWorker:
    def __init__(
        self,
        repository: ReconciliationRepository,
        outcomes: OperationOutcomeQuery,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        max_attempts: int = 5,
        retry_base_seconds: int = 2,
        fault_injector: FaultInjector | None = None,
    ) -> None:
        self._repository = repository
        self._outcomes = outcomes
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_base = retry_base_seconds
        self._faults = fault_injector or NoOpFaultInjector()

    async def run_once(self, *, now: datetime, limit: int = 20) -> int:
        cases = await self._repository.claim(
            self._worker_id, now=now, lease_seconds=self._lease_seconds, limit=limit
        )
        for case in cases:
            resolved = False
            try:
                await self._faults.hit(case.operation_id, FaultPoint.BEFORE_RECONCILIATION_QUERY)
                outcome = await self._outcomes.get_operation_outcome(case)
                await self._faults.hit(
                    case.operation_id, FaultPoint.AFTER_RECONCILIATION_QUERY_BEFORE_RESOLUTION
                )
                await self._repository.resolve(case, outcome, now=now)
                resolved = True
                await self._faults.hit(
                    case.operation_id, FaultPoint.AFTER_RESOLUTION_COMMIT_BEFORE_ACK
                )
            except Exception as exc:
                if resolved:
                    raise
                if case.attempt_count >= self._max_attempts:
                    await self._repository.resolve(
                        case,
                        OperationOutcome(
                            status=OutcomeStatus.INCONSISTENT,
                            operation_id=case.operation_id,
                            action=case.action,
                            reason_code="EVIDENCE_UNAVAILABLE",
                        ),
                        now=now,
                    )
                    continue
                await self._repository.retry(
                    case,
                    now=now,
                    available_at=now
                    + timedelta(seconds=self._retry_base * 2 ** max(0, case.attempt_count - 1)),
                    error_code=getattr(exc, "code", "EVIDENCE_QUERY_FAILED"),
                )
        return len(cases)

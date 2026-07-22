"""Narrow ports for reconciliation persistence and authoritative evidence."""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.reconciliation.models import (
    ClaimedCase,
    CreateCase,
    OperationOutcome,
    ReconciliationCaseView,
)


class StaleReconciliationClaim(RuntimeError):
    code = "STALE_RECONCILIATION_CLAIM"


class ReconciliationRepository(Protocol):
    async def create_or_get(self, command: CreateCase) -> ReconciliationCaseView: ...
    async def get(self, case_id: UUID) -> ReconciliationCaseView | None: ...
    async def claim(
        self, worker_id: str, *, now: datetime, lease_seconds: int, limit: int
    ) -> tuple[ClaimedCase, ...]: ...
    async def resolve(
        self, claim: ClaimedCase, outcome: OperationOutcome, *, now: datetime
    ) -> ReconciliationCaseView: ...
    async def retry(
        self, claim: ClaimedCase, *, now: datetime, available_at: datetime, error_code: str
    ) -> None: ...


class OperationOutcomeQuery(Protocol):
    async def get_operation_outcome(self, case: ClaimedCase) -> OperationOutcome: ...

"""Strict safe reconciliation contracts."""

import hashlib
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import ActorType
from app.infrastructure.database.models.reconciliation import (
    ReconciliationAction,
    ReconciliationStatus,
)


class ReconciliationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class OutcomeStatus(StrEnum):
    COMMITTED = "COMMITTED"
    NOT_COMMITTED = "NOT_COMMITTED"
    INCONSISTENT = "INCONSISTENT"


class OperationOutcome(ReconciliationModel):
    status: OutcomeStatus
    operation_id: UUID
    action: ReconciliationAction
    safe_result: dict[str, object] | None = None
    reason_code: str | None = None


class CreateCase(ReconciliationModel):
    operation_id: UUID
    action: ReconciliationAction
    thread_id: UUID | None = None
    original_run_id: UUID
    original_trace_id: UUID
    operation_idempotency_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    actor_type: ActorType
    actor_id: UUID
    user_id: UUID
    property_id: UUID
    target_entity_type: str | None = None
    target_entity_id: UUID | None = None
    expected_entity_version: int | None = Field(default=None, ge=1)

    @property
    def case_key(self) -> str:
        return hashlib.sha256(
            f"{self.operation_id}|{self.action.value}|{self.request_fingerprint}".encode()
        ).hexdigest()


class ReconciliationCaseView(ReconciliationModel):
    id: UUID
    operation_id: UUID
    action: ReconciliationAction
    thread_id: UUID | None
    property_id: UUID
    target_entity_id: UUID | None
    status: ReconciliationStatus
    last_evidence_status: str | None
    attempt_count: int
    available_at: datetime
    resolution_code: str | None
    safe_result: dict[str, object] | None
    created_at: datetime
    updated_at: datetime


class ClaimedCase(ReconciliationCaseView):
    claimed_by: str
    claim_token: UUID
    original_run_id: UUID
    original_trace_id: UUID
    request_fingerprint: str
    actor_type: ActorType
    actor_id: UUID
    user_id: UUID

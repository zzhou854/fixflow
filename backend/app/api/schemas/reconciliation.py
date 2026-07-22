from datetime import datetime
from uuid import UUID

from app.api.schemas.common import ApiModel
from app.infrastructure.database.models.reconciliation import (
    ReconciliationAction,
    ReconciliationStatus,
)


class ReconciliationCaseResponse(ApiModel):
    case_id: UUID
    operation_id_short: str
    thread_id_short: str | None
    original_run_id_short: str
    operation_type: ReconciliationAction
    status: ReconciliationStatus
    target_entity_type: str | None
    target_entity_id: UUID | None
    expected_entity_version: int | None
    attempt_count: int
    evidence_status: str | None
    last_error_code: str | None
    resolution_code: str | None
    safe_result: dict[str, object] | None
    retry_allowed: bool
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class ReconciliationCasePage(ApiModel):
    items: tuple[ReconciliationCaseResponse, ...]
    limit: int
    offset: int

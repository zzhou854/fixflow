"""Read-only deterministic recovery recommendation rules."""

from app.domain.enums import WorkflowStage
from app.infrastructure.database.models.reconciliation import ReconciliationStatus
from app.replay.enums import (
    RecoveryRecommendation,
    ReplayBundleStatus,
    ReplayExecutionStatus,
)
from app.replay.models import ReplayBundleView, ReplayExecutionView


def recommend_recovery(
    bundle: ReplayBundleView,
    execution: ReplayExecutionView | None,
    *,
    replay_status: ReplayExecutionStatus | None = None,
    reconciliation_status: ReconciliationStatus | None = None,
) -> RecoveryRecommendation:
    if bundle.status in {
        ReplayBundleStatus.INCOMPLETE,
        ReplayBundleStatus.INVALID,
        ReplayBundleStatus.UNAVAILABLE,
    }:
        return RecoveryRecommendation.REPLAY_EVIDENCE_INCOMPLETE
    effective_status = replay_status or (execution.status if execution is not None else None)
    if effective_status is ReplayExecutionStatus.DIVERGED:
        return RecoveryRecommendation.REPLAY_DIVERGED_REVIEW_REQUIRED
    if effective_status in {
        ReplayExecutionStatus.INCOMPLETE,
        ReplayExecutionStatus.UNSUPPORTED_SCHEMA,
        ReplayExecutionStatus.FAILED_SAFE,
    }:
        return RecoveryRecommendation.REPLAY_EVIDENCE_INCOMPLETE
    if reconciliation_status in {
        ReconciliationStatus.PENDING,
        ReconciliationStatus.PROCESSING,
    }:
        return RecoveryRecommendation.WAIT_FOR_RECONCILIATION
    if reconciliation_status is ReconciliationStatus.RESOLVED_NOT_COMMITTED:
        return RecoveryRecommendation.OPERATOR_RECHECK_ALLOWED
    if reconciliation_status is ReconciliationStatus.MANUAL_REVIEW:
        return RecoveryRecommendation.MANUAL_REVIEW_REQUIRED
    expected = bundle.expected_result
    if expected is not None and expected.workflow_stage in {
        WorkflowStage.NEED_INFO,
        WorkflowStage.AWAITING_SLOT_CONFIRMATION,
    }:
        return RecoveryRecommendation.OWNER_RESUME_REQUIRED
    return RecoveryRecommendation.NO_ACTION_REQUIRED

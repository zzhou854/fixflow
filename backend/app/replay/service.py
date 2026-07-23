"""Operator-triggered, idempotent replay verification use case."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from uuid import UUID

from app.replay.canonical import sha256_fingerprint
from app.replay.engine import ReplayEngine
from app.replay.enums import ReplayBundleStatus, ReplayExecutionStatus
from app.replay.models import ReplayBundleView, ReplayExecutionView
from app.replay.recommendation import recommend_recovery
from app.replay.repository import ReplayConflict, ReplayRepository


class ReplayVerificationService:
    def __init__(
        self,
        repository: ReplayRepository,
        engine: ReplayEngine,
        *,
        runtime_revision: str,
        graph_schema_version: int,
        timeout_seconds: float,
    ) -> None:
        self._repository = repository
        self._engine = engine
        self._runtime_revision = runtime_revision
        self._graph_schema_version = graph_schema_version
        self._timeout_seconds = timeout_seconds

    async def verify(
        self,
        bundle: ReplayBundleView,
        *,
        actor_id: UUID,
        user_id: UUID,
        idempotency_key: str,
    ) -> ReplayExecutionView:
        if bundle.status is not ReplayBundleStatus.READY:
            raise ReplayConflict("only a READY bundle can be verified")
        key_fingerprint = hashlib.sha256(idempotency_key.strip().encode()).hexdigest()
        request_fingerprint = sha256_fingerprint(
            {"bundle_id": bundle.bundle_id, "operation": "VERIFY_REPLAY"}
        )
        execution, created = await self._repository.create_execution(
            bundle_id=bundle.bundle_id,
            actor_id=actor_id,
            user_id=user_id,
            request_key_fingerprint=key_fingerprint,
            request_fingerprint=request_fingerprint,
            runtime_revision=self._runtime_revision,
            graph_schema_version=self._graph_schema_version,
            started_at=datetime.now(UTC),
        )
        if not created or execution.status is not ReplayExecutionStatus.RUNNING:
            return execution
        try:
            async with asyncio.timeout(self._timeout_seconds):
                comparison = await self._engine.execute(bundle, execution_id=execution.execution_id)
        except TimeoutError:
            from app.replay.enums import ReplayMismatchType
            from app.replay.models import ReplayComparisonResult, ReplayMismatch

            comparison = ReplayComparisonResult(
                status=ReplayExecutionStatus.FAILED_SAFE,
                node_path=(),
                mismatches=(
                    ReplayMismatch(
                        mismatch_type=ReplayMismatchType.UNEXPECTED_REPLAY_CALL,
                        actual_summary="REPLAY_EXECUTION_TIMEOUT",
                    ),
                ),
                consumed_steps=0,
                total_steps=bundle.step_count,
            )
        recommendation = recommend_recovery(bundle, execution, replay_status=comparison.status)
        return await self._repository.complete_execution(
            execution.execution_id,
            comparison=comparison,
            recommendation=recommendation,
            completed_at=datetime.now(UTC),
            error_code=(
                None
                if comparison.status
                in {ReplayExecutionStatus.PASSED, ReplayExecutionStatus.DIVERGED}
                else comparison.status.value
            ),
        )

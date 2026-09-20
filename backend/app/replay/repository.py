"""Focused SQLAlchemy persistence for replay bundles, tapes, and executions."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.models.observability import (
    AgentRunTrigger,
    AgentTraceEvent,
    TraceSource,
)
from app.infrastructure.database.models.replay import (
    AgentReplayBundle,
    AgentReplayExecution,
    AgentReplayStep,
)
from app.replay.canonical import canonical_json, sha256_fingerprint
from app.replay.enums import (
    RecoveryRecommendation,
    ReplayBundleStatus,
    ReplayExecutionStatus,
    ReplayStepKind,
)
from app.replay.models import (
    REPLAY_INPUT_ADAPTER,
    ReplayBundleView,
    ReplayComparisonResult,
    ReplayExecutionView,
    ReplayInputEnvelope,
    ReplayMismatch,
    ReplaySafeAgentState,
)
from app.replay.steps import REPLAY_STEP_ADAPTER, ReplayStepPayload, ReplayStepRecord


class ReplayConflict(RuntimeError):
    code = "REPLAY_CONFLICT"


class ReplayIdempotencyConflict(ReplayConflict):
    code = "IDEMPOTENCY_CONFLICT"


class ReplayRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        max_steps: int = 500,
        max_payload_bytes: int = 262_144,
    ) -> None:
        self._sessions = sessions
        self._max_steps = max_steps
        self._max_payload_bytes = max_payload_bytes

    async def create_bundle(
        self,
        *,
        original_run_id: UUID,
        thread_id: UUID | None,
        original_trace_id: UUID,
        trigger_type: AgentRunTrigger,
        input_envelope: ReplayInputEnvelope,
        schema_version: int,
        graph_schema_version: int,
        runtime_revision: str,
        captured_at: datetime,
    ) -> ReplayBundleView:
        if len(canonical_json(input_envelope).encode("utf-8")) > self._max_payload_bytes:
            raise ReplayConflict("replay input envelope exceeds the configured size limit")
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(AgentReplayBundle).where(
                    AgentReplayBundle.original_run_id == original_run_id
                )
            )
            if existing is not None:
                return self._bundle_view(existing)
            row = AgentReplayBundle(
                id=uuid4(),
                bundle_id=uuid4(),
                original_run_id=original_run_id,
                thread_id=thread_id,
                original_trace_id=original_trace_id,
                trigger_type=trigger_type,
                status=ReplayBundleStatus.CAPTURING,
                schema_version=schema_version,
                graph_schema_version=graph_schema_version,
                runtime_revision=runtime_revision,
                start_state_payload=None,
                input_envelope=input_envelope.model_dump(mode="json"),
                expected_result_payload=None,
                expected_route_fingerprint=None,
                expected_state_fingerprint=None,
                step_count=0,
                bundle_checksum=None,
                capture_error_code=None,
                captured_at=captured_at,
                finalized_at=None,
            )
            session.add(row)
            session.add(
                self._runless_trace_event(
                    event_type="replay_bundle_started",
                    trace_id=original_trace_id,
                    thread_id=thread_id,
                    original_run_id=original_run_id,
                    occurred_at=captured_at,
                    summary="capture started",
                )
            )
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                async with self._sessions() as retry:
                    found = await retry.scalar(
                        select(AgentReplayBundle).where(
                            AgentReplayBundle.original_run_id == original_run_id
                        )
                    )
                    if found is None:
                        raise
                    return self._bundle_view(found)
            return self._bundle_view(row)

    async def set_start_state(
        self, bundle_id: UUID, state: ReplaySafeAgentState
    ) -> ReplayBundleView:
        async with self._sessions() as session, session.begin():
            row = await self._locked_bundle(session, bundle_id)
            if row.status is not ReplayBundleStatus.CAPTURING:
                return self._bundle_view(row)
            if row.start_state_payload is None:
                row.start_state_payload = state.model_dump(mode="json")
                row.updated_at = datetime.now(UTC)
            await session.flush()
            return self._bundle_view(row)

    async def append_step(
        self,
        bundle_id: UUID,
        *,
        step_key: str,
        payload: ReplayStepPayload,
        request_fingerprint: str | None,
        occurred_at: datetime,
    ) -> ReplayStepRecord:
        if len(canonical_json(payload).encode("utf-8")) > self._max_payload_bytes:
            raise ReplayConflict("replay step exceeds the configured payload size limit")
        async with self._sessions() as session, session.begin():
            bundle = await self._locked_bundle(session, bundle_id)
            if bundle.status is not ReplayBundleStatus.CAPTURING:
                raise ReplayConflict("cannot append to a finalized replay bundle")
            if bundle.step_count >= self._max_steps:
                raise ReplayConflict("replay bundle exceeds the configured step limit")
            existing = await session.scalar(
                select(AgentReplayStep).where(
                    AgentReplayStep.bundle_id == bundle_id,
                    AgentReplayStep.step_key == step_key,
                )
            )
            if existing is not None:
                candidate_checksum = self._step_checksum(
                    sequence=existing.sequence_number,
                    step_kind=payload.kind,
                    step_key=step_key,
                    request_fingerprint=request_fingerprint,
                    response_schema=type(payload).__name__,
                    payload=payload,
                )
                if existing.step_checksum != candidate_checksum:
                    raise ReplayConflict("same replay step key was used with different content")
                return self._step_record(existing)
            sequence = bundle.step_count + 1
            response_schema = type(payload).__name__
            checksum = self._step_checksum(
                sequence=sequence,
                step_kind=payload.kind,
                step_key=step_key,
                request_fingerprint=request_fingerprint,
                response_schema=response_schema,
                payload=payload,
            )
            row = AgentReplayStep(
                id=uuid4(),
                bundle_id=bundle_id,
                sequence_number=sequence,
                step_kind=payload.kind,
                step_key=step_key,
                request_fingerprint=request_fingerprint,
                response_schema=response_schema,
                response_payload=payload.model_dump(mode="json"),
                step_checksum=checksum,
                occurred_at=occurred_at,
            )
            session.add(row)
            bundle.step_count = sequence
            bundle.updated_at = datetime.now(UTC)
            await session.flush()
            return self._step_record(row)

    async def finalize_bundle(
        self,
        bundle_id: UUID,
        *,
        status: ReplayBundleStatus,
        expected_result: ReplaySafeAgentState | None,
        expected_route_fingerprint: str | None,
        expected_state_fingerprint: str | None,
        capture_error_code: str | None,
        finalized_at: datetime,
    ) -> ReplayBundleView:
        if status is ReplayBundleStatus.CAPTURING:
            raise ValueError("final status cannot be CAPTURING")
        async with self._sessions() as session, session.begin():
            row = await self._locked_bundle(session, bundle_id)
            if row.status is not ReplayBundleStatus.CAPTURING:
                return self._bundle_view(row)
            rows = tuple(
                (
                    await session.scalars(
                        select(AgentReplayStep)
                        .where(AgentReplayStep.bundle_id == bundle_id)
                        .order_by(AgentReplayStep.sequence_number)
                    )
                ).all()
            )
            row.status = status
            row.expected_result_payload = (
                expected_result.model_dump(mode="json") if expected_result is not None else None
            )
            row.expected_route_fingerprint = expected_route_fingerprint
            row.expected_state_fingerprint = expected_state_fingerprint
            row.capture_error_code = capture_error_code
            row.finalized_at = finalized_at
            row.updated_at = finalized_at
            if status is ReplayBundleStatus.READY:
                row.bundle_checksum = self._bundle_checksum(row, rows)
            event_type = (
                "replay_bundle_ready"
                if status is ReplayBundleStatus.READY
                else "replay_bundle_incomplete"
            )
            session.add(
                self._runless_trace_event(
                    event_type=event_type,
                    trace_id=row.original_trace_id,
                    thread_id=row.thread_id,
                    original_run_id=row.original_run_id,
                    occurred_at=finalized_at,
                    summary=(
                        f"steps={row.step_count}"
                        if status is ReplayBundleStatus.READY
                        else (capture_error_code or "capture incomplete")
                    ),
                )
            )
            if status is not ReplayBundleStatus.READY:
                session.add(
                    self._runless_trace_event(
                        event_type="replay_capture_failed",
                        trace_id=row.original_trace_id,
                        thread_id=row.thread_id,
                        original_run_id=row.original_run_id,
                        occurred_at=finalized_at,
                        summary=capture_error_code or "capture incomplete",
                    )
                )
            await session.flush()
            return self._bundle_view(row)

    async def mark_incomplete(
        self, bundle_id: UUID, *, error_code: str, occurred_at: datetime
    ) -> ReplayBundleView:
        return await self.finalize_bundle(
            bundle_id,
            status=ReplayBundleStatus.INCOMPLETE,
            expected_result=None,
            expected_route_fingerprint=None,
            expected_state_fingerprint=None,
            capture_error_code=error_code[:80],
            finalized_at=occurred_at,
        )

    async def invalidate_bundle(
        self, bundle_id: UUID, *, error_code: str, occurred_at: datetime
    ) -> ReplayBundleView:
        async with self._sessions() as session, session.begin():
            row = await self._locked_bundle(session, bundle_id)
            row.status = ReplayBundleStatus.INVALID
            row.capture_error_code = error_code[:80]
            row.finalized_at = row.finalized_at or occurred_at
            row.updated_at = occurred_at
            await session.flush()
            return self._bundle_view(row)

    async def get_bundle_for_run(self, run_id: UUID) -> ReplayBundleView | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(AgentReplayBundle).where(AgentReplayBundle.original_run_id == run_id)
            )
            return self._bundle_view(row) if row is not None else None

    async def get_bundle(self, bundle_id: UUID) -> ReplayBundleView | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(AgentReplayBundle).where(AgentReplayBundle.bundle_id == bundle_id)
            )
            return self._bundle_view(row) if row is not None else None

    async def list_bundles_for_thread(
        self, thread_id: UUID, *, limit: int, offset: int
    ) -> tuple[ReplayBundleView, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(AgentReplayBundle)
                .where(AgentReplayBundle.thread_id == thread_id)
                .order_by(AgentReplayBundle.captured_at.desc(), AgentReplayBundle.bundle_id.desc())
                .limit(limit)
                .offset(offset)
            )
            return tuple(self._bundle_view(row) for row in rows)

    async def load_steps(self, bundle_id: UUID) -> tuple[ReplayStepRecord, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(AgentReplayStep)
                .where(AgentReplayStep.bundle_id == bundle_id)
                .order_by(AgentReplayStep.sequence_number)
            )
            return tuple(self._step_record(row) for row in rows)

    async def create_execution(
        self,
        *,
        bundle_id: UUID,
        actor_id: UUID,
        user_id: UUID,
        request_key_fingerprint: str,
        request_fingerprint: str,
        runtime_revision: str,
        graph_schema_version: int,
        started_at: datetime,
    ) -> tuple[ReplayExecutionView, bool]:
        async with self._sessions() as session, session.begin():
            existing = await session.scalar(
                select(AgentReplayExecution).where(
                    AgentReplayExecution.requested_by_actor_id == actor_id,
                    AgentReplayExecution.request_key_fingerprint == request_key_fingerprint,
                )
            )
            if existing is not None:
                if (
                    existing.request_fingerprint != request_fingerprint
                    or existing.bundle_id != bundle_id
                ):
                    raise ReplayIdempotencyConflict("same key was used for another replay request")
                return self._execution_view(existing), False
            bundle = await session.scalar(
                select(AgentReplayBundle).where(AgentReplayBundle.bundle_id == bundle_id)
            )
            row = AgentReplayExecution(
                id=uuid4(),
                execution_id=uuid4(),
                bundle_id=bundle_id,
                requested_by_actor_id=actor_id,
                requested_by_user_id=user_id,
                request_key_fingerprint=request_key_fingerprint,
                request_fingerprint=request_fingerprint,
                status=ReplayExecutionStatus.RUNNING,
                runtime_revision=runtime_revision,
                graph_schema_version=graph_schema_version,
                started_at=started_at,
                completed_at=None,
                actual_route_fingerprint=None,
                actual_state_fingerprint=None,
                mismatch_count=0,
                comparison_summary=None,
                error_code=None,
                recommendation=None,
                safe_error_message=None,
            )
            session.add(row)
            if bundle is not None:
                for event_type, summary in (
                    ("replay_requested", "operator requested deterministic verification"),
                    ("replay_started", "tape-only verification started"),
                ):
                    session.add(
                        AgentTraceEvent(
                            id=uuid4(),
                            event_key=hashlib.sha256(
                                f"{row.execution_id}|{event_type}".encode()
                            ).hexdigest(),
                            run_id=None,
                            thread_id=bundle.thread_id,
                            trace_id=row.execution_id,
                            sequence_number=None,
                            source=TraceSource.REPLAY,
                            event_type=event_type,
                            node_name=None,
                            operation_id=None,
                            payload={
                                "original_run_id": str(bundle.original_run_id),
                                "summary": summary,
                            },
                            occurred_at=started_at,
                        )
                    )
            try:
                await session.flush()
            except IntegrityError:
                await session.rollback()
                async with self._sessions() as retry:
                    found = await retry.scalar(
                        select(AgentReplayExecution).where(
                            AgentReplayExecution.requested_by_actor_id == actor_id,
                            AgentReplayExecution.request_key_fingerprint == request_key_fingerprint,
                        )
                    )
                    if found is None:
                        raise
                    if (
                        found.request_fingerprint != request_fingerprint
                        or found.bundle_id != bundle_id
                    ):
                        raise ReplayIdempotencyConflict(
                            "same key was used for another replay request"
                        ) from None
                    return self._execution_view(found), False
            return self._execution_view(row), True

    async def complete_execution(
        self,
        execution_id: UUID,
        *,
        comparison: ReplayComparisonResult,
        recommendation: RecoveryRecommendation,
        completed_at: datetime,
        error_code: str | None = None,
    ) -> ReplayExecutionView:
        async with self._sessions() as session, session.begin():
            row = await session.scalar(
                select(AgentReplayExecution)
                .where(AgentReplayExecution.execution_id == execution_id)
                .with_for_update()
            )
            if row is None:
                raise ReplayConflict("replay execution not found")
            if row.status is not ReplayExecutionStatus.RUNNING:
                return self._execution_view(row)
            row.status = comparison.status
            row.completed_at = completed_at
            row.actual_route_fingerprint = comparison.route_fingerprint
            row.actual_state_fingerprint = comparison.state_fingerprint
            row.mismatch_count = len(comparison.mismatches)
            row.comparison_summary = comparison.model_dump(mode="json")
            row.error_code = error_code
            row.recommendation = recommendation.value
            row.updated_at = completed_at
            bundle = await session.scalar(
                select(AgentReplayBundle).where(AgentReplayBundle.bundle_id == row.bundle_id)
            )
            event_type = {
                ReplayExecutionStatus.PASSED: "replay_passed",
                ReplayExecutionStatus.DIVERGED: "replay_diverged",
                ReplayExecutionStatus.INCOMPLETE: "replay_incomplete",
                ReplayExecutionStatus.UNSUPPORTED_SCHEMA: "replay_unsupported_schema",
                ReplayExecutionStatus.FAILED_SAFE: "replay_failed_safe",
            }[comparison.status]
            event_key = hashlib.sha256(f"{execution_id}|{event_type}".encode()).hexdigest()
            session.add(
                AgentTraceEvent(
                    id=uuid4(),
                    event_key=event_key,
                    run_id=None,
                    thread_id=None,
                    trace_id=execution_id,
                    sequence_number=None,
                    source=TraceSource.REPLAY,
                    event_type=event_type,
                    node_name=None,
                    operation_id=None,
                    payload={
                        "status": comparison.status.value,
                        "summary": f"mismatches={len(comparison.mismatches)}",
                        "original_run_id": (
                            str(bundle.original_run_id) if bundle is not None else None
                        ),
                    },
                    occurred_at=completed_at,
                )
            )
            await session.flush()
            return self._execution_view(row)

    @staticmethod
    def _runless_trace_event(
        *,
        event_type: str,
        trace_id: UUID,
        thread_id: UUID | None,
        original_run_id: UUID,
        occurred_at: datetime,
        summary: str,
    ) -> AgentTraceEvent:
        event_key = hashlib.sha256(f"{original_run_id}|{event_type}".encode()).hexdigest()
        return AgentTraceEvent(
            id=uuid4(),
            event_key=event_key,
            run_id=None,
            thread_id=thread_id,
            trace_id=trace_id,
            sequence_number=None,
            source=TraceSource.REPLAY,
            event_type=event_type,
            node_name=None,
            operation_id=None,
            payload={
                "original_run_id": str(original_run_id),
                "summary": summary[:200],
            },
            occurred_at=occurred_at,
        )

    async def get_execution(self, execution_id: UUID) -> ReplayExecutionView | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(AgentReplayExecution).where(
                    AgentReplayExecution.execution_id == execution_id
                )
            )
            return self._execution_view(row) if row is not None else None

    async def latest_execution(self, bundle_id: UUID) -> ReplayExecutionView | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(AgentReplayExecution)
                .where(AgentReplayExecution.bundle_id == bundle_id)
                .order_by(AgentReplayExecution.started_at.desc())
                .limit(1)
            )
            return self._execution_view(row) if row is not None else None

    async def validate_integrity(
        self, bundle: ReplayBundleView, steps: tuple[ReplayStepRecord, ...]
    ) -> bool:
        if bundle.bundle_checksum is None or bundle.start_state is None:
            return False
        if [step.sequence_number for step in steps] != list(range(1, len(steps) + 1)):
            return False
        for step in steps:
            expected = self._step_checksum(
                sequence=step.sequence_number,
                step_kind=step.step_kind,
                step_key=step.step_key,
                request_fingerprint=step.request_fingerprint,
                response_schema=step.response_schema,
                payload=step.payload,
            )
            if expected != step.step_checksum:
                return False
        async with self._sessions() as session:
            row = await session.scalar(
                select(AgentReplayBundle).where(AgentReplayBundle.bundle_id == bundle.bundle_id)
            )
            if row is None:
                return False
            stored_steps = tuple(
                (
                    await session.scalars(
                        select(AgentReplayStep)
                        .where(AgentReplayStep.bundle_id == bundle.bundle_id)
                        .order_by(AgentReplayStep.sequence_number)
                    )
                ).all()
            )
            return self._bundle_checksum(row, stored_steps) == bundle.bundle_checksum

    @staticmethod
    async def _locked_bundle(session: AsyncSession, bundle_id: UUID) -> AgentReplayBundle:
        row = await session.scalar(
            select(AgentReplayBundle)
            .where(AgentReplayBundle.bundle_id == bundle_id)
            .with_for_update()
        )
        if row is None:
            raise ReplayConflict("replay bundle not found")
        return row

    @staticmethod
    def _step_checksum(
        *,
        sequence: int,
        step_kind: ReplayStepKind,
        step_key: str,
        request_fingerprint: str | None,
        response_schema: str,
        payload: ReplayStepPayload,
    ) -> str:
        return sha256_fingerprint(
            {
                "sequence_number": sequence,
                "step_kind": step_kind.value,
                "step_key": step_key,
                "request_fingerprint": request_fingerprint,
                "response_schema": response_schema,
                "response_payload": payload.model_dump(mode="json"),
            }
        )

    @staticmethod
    def _bundle_checksum(row: AgentReplayBundle, steps: tuple[AgentReplayStep, ...]) -> str:
        return sha256_fingerprint(
            {
                "bundle_id": row.bundle_id,
                "original_run_id": row.original_run_id,
                "thread_id": row.thread_id,
                "original_trace_id": row.original_trace_id,
                "trigger_type": row.trigger_type.value,
                "schema_version": row.schema_version,
                "graph_schema_version": row.graph_schema_version,
                "runtime_revision": row.runtime_revision,
                "start_state_payload": row.start_state_payload,
                "input_envelope": row.input_envelope,
                "expected_result_payload": row.expected_result_payload,
                "expected_route_fingerprint": row.expected_route_fingerprint,
                "expected_state_fingerprint": row.expected_state_fingerprint,
                "step_count": row.step_count,
                "step_checksums": [step.step_checksum for step in steps],
            }
        )

    @staticmethod
    def _bundle_view(row: AgentReplayBundle) -> ReplayBundleView:
        return ReplayBundleView(
            bundle_id=row.bundle_id,
            original_run_id=row.original_run_id,
            thread_id=row.thread_id,
            original_trace_id=row.original_trace_id,
            trigger_type=row.trigger_type,
            status=row.status,
            schema_version=row.schema_version,
            graph_schema_version=row.graph_schema_version,
            runtime_revision=row.runtime_revision,
            start_state=(
                ReplaySafeAgentState.model_validate(row.start_state_payload)
                if row.start_state_payload is not None
                else None
            ),
            input_envelope=REPLAY_INPUT_ADAPTER.validate_python(row.input_envelope),
            expected_result=(
                ReplaySafeAgentState.model_validate(row.expected_result_payload)
                if row.expected_result_payload is not None
                else None
            ),
            expected_route_fingerprint=row.expected_route_fingerprint,
            expected_state_fingerprint=row.expected_state_fingerprint,
            step_count=row.step_count,
            bundle_checksum=row.bundle_checksum,
            capture_error_code=row.capture_error_code,
            captured_at=row.captured_at,
            finalized_at=row.finalized_at,
        )

    @staticmethod
    def _step_record(row: AgentReplayStep) -> ReplayStepRecord:
        payload = REPLAY_STEP_ADAPTER.validate_python(row.response_payload)
        return ReplayStepRecord(
            sequence_number=row.sequence_number,
            step_kind=row.step_kind,
            step_key=row.step_key,
            request_fingerprint=row.request_fingerprint,
            response_schema=row.response_schema,
            payload=payload,
            step_checksum=row.step_checksum,
        )

    @staticmethod
    def _execution_view(row: AgentReplayExecution) -> ReplayExecutionView:
        mismatches: tuple[ReplayMismatch, ...] = ()
        if row.comparison_summary:
            result = ReplayComparisonResult.model_validate(row.comparison_summary)
            mismatches = result.mismatches
        return ReplayExecutionView(
            execution_id=row.execution_id,
            bundle_id=row.bundle_id,
            requested_by_actor_id=row.requested_by_actor_id,
            requested_by_user_id=row.requested_by_user_id,
            status=row.status,
            runtime_revision=row.runtime_revision,
            graph_schema_version=row.graph_schema_version,
            started_at=row.started_at,
            completed_at=row.completed_at,
            actual_route_fingerprint=row.actual_route_fingerprint,
            actual_state_fingerprint=row.actual_state_fingerprint,
            mismatches=mismatches,
            error_code=row.error_code,
            recommendation=(
                RecoveryRecommendation(row.recommendation) if row.recommendation else None
            ),
        )

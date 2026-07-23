"""Formal Operator authorization and safe Recovery Console projections."""

from __future__ import annotations

from uuid import UUID

from app.api.schemas.replay import (
    ReplayBundleResponse,
    ReplayExecutionResponse,
    ReplayMismatchResponse,
    ReplayRunDetailResponse,
    ReplayRunItemResponse,
)
from app.api.services.operator_trace import OperatorTraceQueryService
from app.application.auth import AuthenticatedIdentity
from app.application.query_models import QueryActor
from app.application.services import FixFlowApplicationService
from app.domain.enums import ActorType
from app.infrastructure.database.models.observability import AgentRunTrigger
from app.replay.enums import ReplayBundleStatus
from app.replay.models import (
    OperatorActionReplayInput,
    ReplayBundleView,
    ReplayExecutionView,
)
from app.replay.repository import ReplayRepository
from app.replay.service import ReplayVerificationService
from app.trace.models import AgentRunRecord
from app.trace.runtime import TraceRuntime


class OperatorReplayService:
    def __init__(
        self,
        repository: ReplayRepository,
        verification: ReplayVerificationService,
        operator_trace: OperatorTraceQueryService,
        trace: TraceRuntime,
        application: FixFlowApplicationService,
    ) -> None:
        self._repository = repository
        self._verification = verification
        self._operator_trace = operator_trace
        self._trace = trace
        self._application = application

    async def list_for_thread(
        self,
        identity: AuthenticatedIdentity,
        thread_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[ReplayRunItemResponse, ...] | None:
        runs = await self._operator_trace.list_runs(identity, thread_id, limit=limit, offset=offset)
        if runs is None:
            return None
        items: list[ReplayRunItemResponse] = []
        for run in runs:
            bundle = await self._repository.get_bundle_for_run(run.run_id)
            latest = (
                await self._repository.latest_execution(bundle.bundle_id)
                if bundle is not None
                else None
            )
            items.append(
                ReplayRunItemResponse(
                    run_id=run.run_id,
                    thread_id=run.thread_id,
                    trigger_type=run.trigger,
                    original_run_status=run.status,
                    replayability=(
                        bundle.status if bundle is not None else ReplayBundleStatus.UNAVAILABLE
                    ),
                    bundle_id=bundle.bundle_id if bundle else None,
                    bundle_status=(
                        bundle.status if bundle is not None else ReplayBundleStatus.UNAVAILABLE
                    ),
                    latest_replay_status=latest.status if latest else None,
                    started_at=run.started_at,
                )
            )
        return tuple(items)

    async def get_run(
        self, identity: AuthenticatedIdentity, run_id: UUID
    ) -> ReplayRunDetailResponse | None:
        run = await self._authorize_run(identity, run_id)
        if run is None:
            return None
        bundle = await self._repository.get_bundle_for_run(run_id)
        latest = (
            await self._repository.latest_execution(bundle.bundle_id)
            if bundle is not None
            else None
        )
        item = ReplayRunItemResponse(
            run_id=run.run_id,
            thread_id=run.thread_id,
            trigger_type=run.trigger,
            original_run_status=run.status,
            replayability=(bundle.status if bundle is not None else ReplayBundleStatus.UNAVAILABLE),
            bundle_id=bundle.bundle_id if bundle else None,
            bundle_status=(bundle.status if bundle is not None else ReplayBundleStatus.UNAVAILABLE),
            latest_replay_status=latest.status if latest else None,
            started_at=run.started_at,
        )
        current = await self._current_state(identity, bundle)
        return ReplayRunDetailResponse(
            run=item,
            bundle=self._bundle_response(bundle, latest) if bundle else None,
            current_business_state=current,
        )

    async def get_bundle(
        self, identity: AuthenticatedIdentity, bundle_id: UUID
    ) -> ReplayBundleResponse | None:
        bundle = await self._repository.get_bundle(bundle_id)
        if bundle is None or await self._authorize_run(identity, bundle.original_run_id) is None:
            return None
        latest = await self._repository.latest_execution(bundle.bundle_id)
        return self._bundle_response(bundle, latest)

    async def verify(
        self,
        identity: AuthenticatedIdentity,
        run_id: UUID,
        *,
        idempotency_key: str,
    ) -> ReplayExecutionResponse | None:
        if await self._authorize_run(identity, run_id) is None:
            return None
        bundle = await self._repository.get_bundle_for_run(run_id)
        if bundle is None:
            return None
        result = await self._verification.verify(
            bundle,
            actor_id=identity.actor_id,
            user_id=identity.user_id,
            idempotency_key=idempotency_key,
        )
        return self._execution_response(result)

    async def get_execution(
        self, identity: AuthenticatedIdentity, execution_id: UUID
    ) -> ReplayExecutionResponse | None:
        execution = await self._repository.get_execution(execution_id)
        if execution is None:
            return None
        bundle = await self._repository.get_bundle(execution.bundle_id)
        if bundle is None or await self._authorize_run(identity, bundle.original_run_id) is None:
            return None
        return self._execution_response(execution)

    async def _authorize_run(
        self, identity: AuthenticatedIdentity, run_id: UUID
    ) -> AgentRunRecord | None:
        if identity.actor_type is not ActorType.OPERATOR:
            return None
        authorized = await self._operator_trace.get_run(identity, run_id)
        if authorized is not None:
            return await self._trace.get_run(run_id)
        raw = await self._trace.get_run(run_id)
        if raw is None or raw.trigger is not AgentRunTrigger.OPERATOR_ACTION:
            return None
        bundle = await self._repository.get_bundle_for_run(run_id)
        if bundle is None or not isinstance(bundle.input_envelope, OperatorActionReplayInput):
            return None
        try:
            await self._application.get_ticket_detail(
                QueryActor(identity.actor_type, identity.actor_id),
                bundle.input_envelope.target_entity_id,
            )
        except Exception:
            return None
        return raw

    async def _current_state(
        self, identity: AuthenticatedIdentity, bundle: ReplayBundleView | None
    ) -> dict[str, str | int | bool | None] | None:
        if bundle is None or bundle.expected_result is None:
            return None
        ticket_id = bundle.expected_result.active_ticket_id
        if ticket_id is None and isinstance(bundle.input_envelope, OperatorActionReplayInput):
            ticket_id = bundle.input_envelope.target_entity_id
        if ticket_id is None:
            return None
        try:
            detail = await self._application.get_ticket_detail(
                QueryActor(identity.actor_type, identity.actor_id), ticket_id
            )
        except Exception:
            return None
        return {
            "ticket_id": str(detail.ticket.ticket_id),
            "ticket_status": detail.ticket.ticket_status.value,
            "ticket_version": detail.ticket.version,
            "appointment_id": (
                str(detail.ticket.appointment.appointment_id)
                if detail.ticket.appointment is not None
                else None
            ),
        }

    @classmethod
    def _bundle_response(
        cls,
        bundle: ReplayBundleView,
        latest: ReplayExecutionView | None,
    ) -> ReplayBundleResponse:
        return ReplayBundleResponse(
            bundle_id=bundle.bundle_id,
            original_run_id=bundle.original_run_id,
            status=bundle.status,
            schema_version=bundle.schema_version,
            graph_schema_version=bundle.graph_schema_version,
            runtime_revision=bundle.runtime_revision,
            artifact_integrity=("CHECKSUM_PRESENT" if bundle.bundle_checksum else "INCOMPLETE"),
            expected_route_fingerprint=bundle.expected_route_fingerprint,
            expected_state_fingerprint=bundle.expected_state_fingerprint,
            step_count=bundle.step_count,
            capture_error_code=bundle.capture_error_code,
            captured_at=bundle.captured_at,
            finalized_at=bundle.finalized_at,
            latest_execution=cls._execution_response(latest) if latest else None,
        )

    @staticmethod
    def _execution_response(execution: ReplayExecutionView) -> ReplayExecutionResponse:
        return ReplayExecutionResponse(
            execution_id=execution.execution_id,
            bundle_id=execution.bundle_id,
            status=execution.status,
            runtime_revision=execution.runtime_revision,
            graph_schema_version=execution.graph_schema_version,
            started_at=execution.started_at,
            completed_at=execution.completed_at,
            actual_route_fingerprint=execution.actual_route_fingerprint,
            actual_state_fingerprint=execution.actual_state_fingerprint,
            mismatches=tuple(
                ReplayMismatchResponse.model_validate(item.model_dump())
                for item in execution.mismatches
            ),
            recommendation=execution.recommendation,
            error_code=execution.error_code,
        )

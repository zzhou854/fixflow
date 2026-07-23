"""Per-run replay capture port and failure-isolated persistent implementation."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from app.agent.state import AgentState
from app.agent_runtime.models import AgentRunResult
from app.agent_runtime.runtime_state import RuntimeGraphState, load_state
from app.infrastructure.database.models.observability import AgentRunTrigger
from app.replay.canonical import sha256_fingerprint
from app.replay.enums import ReplayBundleStatus
from app.replay.models import (
    ReconciliationProjection,
    ReplayBundleView,
    ReplayInputEnvelope,
    ReplaySafeAgentState,
)
from app.replay.repository import ReplayRepository
from app.replay.state import project_agent_state, state_fingerprint
from app.replay.steps import (
    FinalStateStep,
    InterruptResultStep,
    NodeEnteredStep,
    OperatorMutationResultStep,
    ReplayStepPayload,
    RouteDecisionStep,
)

REPLAY_BUNDLE_SCHEMA_VERSION = 1
GRAPH_SCHEMA_VERSION = 1


class ReplayCapturePort(Protocol):
    async def node_entered(self, node_name: str, graph_state: RuntimeGraphState) -> None: ...

    async def node_completed(self, node_name: str, graph_state: RuntimeGraphState) -> None: ...

    async def route_decision(
        self, from_node: str, decision: str, to_node: str, graph_state: RuntimeGraphState
    ) -> None: ...

    async def external_result(
        self,
        *,
        step_key_prefix: str,
        payload: ReplayStepPayload,
        request_fingerprint: str | None,
    ) -> None: ...

    async def finalize(self, result: AgentRunResult) -> ReplayBundleView | None: ...


class NoOpReplayCapture:
    async def node_entered(self, node_name: str, graph_state: RuntimeGraphState) -> None:
        del node_name, graph_state

    async def node_completed(self, node_name: str, graph_state: RuntimeGraphState) -> None:
        del node_name, graph_state

    async def route_decision(
        self, from_node: str, decision: str, to_node: str, graph_state: RuntimeGraphState
    ) -> None:
        del from_node, decision, to_node, graph_state

    async def external_result(
        self,
        *,
        step_key_prefix: str,
        payload: ReplayStepPayload,
        request_fingerprint: str | None,
    ) -> None:
        del step_key_prefix, payload, request_fingerprint

    async def finalize(self, result: AgentRunResult) -> ReplayBundleView | None:
        del result
        return None


class PersistentReplayCapture:
    """Capture one run without ever deciding whether its business work commits."""

    def __init__(
        self,
        repository: ReplayRepository,
        bundle: ReplayBundleView,
    ) -> None:
        self._repository = repository
        self._bundle = bundle
        self._latest_state: AgentState | None = None
        self._node_path: list[str] = []
        self._routes: list[tuple[str, str, str]] = []
        self._counters: dict[str, int] = {}
        self._capture_error: str | None = None

    @property
    def bundle_id(self) -> UUID:
        return self._bundle.bundle_id

    async def node_entered(self, node_name: str, graph_state: RuntimeGraphState) -> None:
        state = load_state(graph_state)
        self._latest_state = state
        if self._bundle.start_state is None:
            await self._guard(
                "REPLAY_START_STATE_CAPTURE_FAILED",
                self._set_start_state(project_agent_state(state)),
            )
        self._node_path.append(node_name)
        safe = project_agent_state(state)
        await self._append(
            f"node:{len(self._node_path)}:{node_name}",
            NodeEnteredStep(node_name=node_name, state_fingerprint=state_fingerprint(safe)),
        )

    async def node_completed(self, node_name: str, graph_state: RuntimeGraphState) -> None:
        del node_name
        try:
            self._latest_state = load_state(graph_state)
        except Exception:
            self._capture_error = self._capture_error or "REPLAY_STATE_CAPTURE_FAILED"

    async def route_decision(
        self, from_node: str, decision: str, to_node: str, graph_state: RuntimeGraphState
    ) -> None:
        safe = project_agent_state(load_state(graph_state))
        self._routes.append((from_node, decision, to_node))
        await self._append(
            f"route:{len(self._routes)}:{from_node}",
            RouteDecisionStep(
                from_node=from_node,
                decision=decision,
                to_node=to_node,
                decision_input_fingerprint=state_fingerprint(safe),
            ),
        )

    async def external_result(
        self,
        *,
        step_key_prefix: str,
        payload: ReplayStepPayload,
        request_fingerprint: str | None,
    ) -> None:
        count = self._counters.get(step_key_prefix, 0) + 1
        self._counters[step_key_prefix] = count
        await self._append(
            f"{step_key_prefix}:{count}",
            payload,
            request_fingerprint=request_fingerprint,
        )

    async def finalize(self, result: AgentRunResult) -> ReplayBundleView | None:
        now = datetime.now(UTC)
        if self._capture_error is not None or self._latest_state is None:
            return await self._repository.mark_incomplete(
                self.bundle_id,
                error_code=self._capture_error or "REPLAY_START_STATE_MISSING",
                occurred_at=now,
            )
        safe = project_agent_state(self._latest_state, run_status=result.run_status.value)
        updates: dict[str, object] = {
            "workflow_stage": result.workflow_stage,
            "active_ticket_id": result.active_ticket_id,
            "active_appointment_id": result.active_appointment_id,
        }
        if (
            result.pending_reconciliation_case_id is not None
            or result.pending_reconciliation_status is not None
        ):
            updates["reconciliation_projection"] = ReconciliationProjection(
                case_id=result.pending_reconciliation_case_id,
                status=result.pending_reconciliation_status,
                action=result.pending_reconciliation_action,
            )
        safe = safe.model_copy(update=updates)
        if result.interrupt is not None:
            candidate = getattr(result.interrupt, "candidates_fingerprint", None)
            await self._append(
                "interrupt:final",
                InterruptResultStep(
                    interrupt=result.interrupt,
                    candidate_fingerprint=candidate,
                ),
            )
        route_fingerprint = sha256_fingerprint({"nodes": self._node_path, "routes": self._routes})
        final_fingerprint = state_fingerprint(safe)
        await self._append(
            "final-state",
            FinalStateStep(
                state=safe,
                state_fingerprint=final_fingerprint,
                route_fingerprint=route_fingerprint,
            ),
        )
        if self._capture_error is not None:
            return await self._repository.mark_incomplete(
                self.bundle_id, error_code=self._capture_error, occurred_at=now
            )
        return await self._repository.finalize_bundle(
            self.bundle_id,
            status=ReplayBundleStatus.READY,
            expected_result=safe,
            expected_route_fingerprint=route_fingerprint,
            expected_state_fingerprint=final_fingerprint,
            capture_error_code=None,
            finalized_at=now,
        )

    async def _set_start_state(self, state: object) -> None:
        self._bundle = await self._repository.set_start_state(
            self.bundle_id,
            state,  # type: ignore[arg-type]
        )

    async def _append(
        self,
        step_key: str,
        payload: ReplayStepPayload,
        *,
        request_fingerprint: str | None = None,
    ) -> None:
        if self._capture_error is not None:
            return
        try:
            await self._repository.append_step(
                self.bundle_id,
                step_key=step_key,
                payload=payload,
                request_fingerprint=request_fingerprint,
                occurred_at=datetime.now(UTC),
            )
        except Exception:
            self._capture_error = "REPLAY_STEP_CAPTURE_FAILED"

    async def _guard(self, code: str, operation: object) -> None:
        try:
            await operation  # type: ignore[misc]
        except Exception:
            self._capture_error = code


class ReplayCaptureService:
    def __init__(
        self,
        repository: ReplayRepository,
        *,
        runtime_revision: str,
        schema_version: int = REPLAY_BUNDLE_SCHEMA_VERSION,
        graph_schema_version: int = GRAPH_SCHEMA_VERSION,
    ) -> None:
        self._repository = repository
        self._runtime_revision = runtime_revision
        self._schema_version = schema_version
        self._graph_schema_version = graph_schema_version

    async def start(
        self,
        *,
        original_run_id: UUID,
        thread_id: UUID | None,
        original_trace_id: UUID,
        trigger_type: AgentRunTrigger,
        input_envelope: ReplayInputEnvelope,
    ) -> ReplayCapturePort:
        try:
            bundle = await self._repository.create_bundle(
                original_run_id=original_run_id,
                thread_id=thread_id,
                original_trace_id=original_trace_id,
                trigger_type=trigger_type,
                input_envelope=input_envelope,
                schema_version=self._schema_version,
                graph_schema_version=self._graph_schema_version,
                runtime_revision=self._runtime_revision,
                captured_at=datetime.now(UTC),
            )
            if bundle.status is not ReplayBundleStatus.CAPTURING:
                return NoOpReplayCapture()
            return PersistentReplayCapture(self._repository, bundle)
        except Exception:
            return NoOpReplayCapture()

    async def capture_operator_action(
        self,
        *,
        original_run_id: UUID,
        original_trace_id: UUID,
        input_envelope: ReplayInputEnvelope,
        start_state: ReplaySafeAgentState,
        mutation: OperatorMutationResultStep,
    ) -> ReplayBundleView | None:
        """Persist a mutation-plan verification bundle without replaying the mutation."""

        try:
            bundle = await self._repository.create_bundle(
                original_run_id=original_run_id,
                thread_id=None,
                original_trace_id=original_trace_id,
                trigger_type=AgentRunTrigger.OPERATOR_ACTION,
                input_envelope=input_envelope,
                schema_version=self._schema_version,
                graph_schema_version=self._graph_schema_version,
                runtime_revision=self._runtime_revision,
                captured_at=datetime.now(UTC),
            )
            safe = start_state
            await self._repository.set_start_state(bundle.bundle_id, safe)
            await self._repository.append_step(
                bundle.bundle_id,
                step_key="operator-mutation:1",
                payload=mutation,
                request_fingerprint=mutation.request_fingerprint,
                occurred_at=datetime.now(UTC),
            )
            route_fingerprint = sha256_fingerprint(
                {"nodes": (), "routes": (), "operator_action": mutation.action}
            )
            final_fingerprint = state_fingerprint(safe)
            await self._repository.append_step(
                bundle.bundle_id,
                step_key="final-state",
                payload=FinalStateStep(
                    state=safe,
                    state_fingerprint=final_fingerprint,
                    route_fingerprint=route_fingerprint,
                ),
                request_fingerprint=None,
                occurred_at=datetime.now(UTC),
            )
            return await self._repository.finalize_bundle(
                bundle.bundle_id,
                status=ReplayBundleStatus.READY,
                expected_result=safe,
                expected_route_fingerprint=route_fingerprint,
                expected_state_fingerprint=final_fingerprint,
                capture_error_code=None,
                finalized_at=datetime.now(UTC),
            )
        except Exception:
            return None


def message_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

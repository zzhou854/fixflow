"""Graph observer and final deterministic comparison."""

from __future__ import annotations

from app.agent_runtime.runtime_state import RuntimeGraphState, load_state
from app.replay.canonical import sha256_fingerprint
from app.replay.enums import ReplayExecutionStatus, ReplayMismatchType, ReplayStepKind
from app.replay.models import ReplayComparisonResult, ReplayMismatch, ReplaySafeAgentState
from app.replay.state import project_agent_state, state_fingerprint
from app.replay.steps import (
    FinalStateStep,
    InterruptResultStep,
    NodeEnteredStep,
    RouteDecisionStep,
)
from app.replay.tape import ReplayTapeCursor, ReplayTapeMiss


class ReplayGraphObserver:
    def __init__(self, cursor: ReplayTapeCursor) -> None:
        self._cursor = cursor
        self.node_path: list[str] = []
        self.routes: list[tuple[str, str, str]] = []
        self.latest_state: ReplaySafeAgentState | None = None

    async def node_entered(self, node_name: str, graph_state: RuntimeGraphState) -> None:
        self.latest_state = project_agent_state(load_state(graph_state))
        self.node_path.append(node_name)
        payload = self._cursor.consume(ReplayStepKind.NODE_ENTERED)
        if not isinstance(payload, NodeEnteredStep):
            return
        if payload.node_name != node_name:
            self._cursor.mismatches.append(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.NODE_SEQUENCE_MISMATCH,
                    expected_summary=payload.node_name,
                    actual_summary=node_name,
                )
            )
        actual_fingerprint = state_fingerprint(self.latest_state)
        if payload.state_fingerprint != actual_fingerprint:
            self._cursor.mismatches.append(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.FINAL_STATE_MISMATCH,
                    expected_summary=payload.state_fingerprint,
                    actual_summary=actual_fingerprint,
                )
            )

    async def node_completed(self, node_name: str, graph_state: RuntimeGraphState) -> None:
        del node_name
        self.latest_state = project_agent_state(load_state(graph_state))

    async def route_decision(
        self, from_node: str, decision: str, to_node: str, graph_state: RuntimeGraphState
    ) -> None:
        actual_fingerprint = state_fingerprint(project_agent_state(load_state(graph_state)))
        self.routes.append((from_node, decision, to_node))
        payload = self._cursor.consume(ReplayStepKind.ROUTE_DECISION)
        if not isinstance(payload, RouteDecisionStep):
            return
        if (payload.from_node, payload.decision, payload.to_node) != (
            from_node,
            decision,
            to_node,
        ):
            self._cursor.mismatches.append(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.ROUTE_DECISION_MISMATCH,
                    expected_summary=f"{payload.from_node}:{payload.decision}:{payload.to_node}",
                    actual_summary=f"{from_node}:{decision}:{to_node}",
                )
            )
        if payload.decision_input_fingerprint != actual_fingerprint:
            self._cursor.mismatches.append(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.ROUTE_DECISION_MISMATCH,
                    step_key=f"route:{payload.from_node}",
                    expected_summary=payload.decision_input_fingerprint,
                    actual_summary=actual_fingerprint,
                )
            )

    async def external_result(
        self,
        *,
        step_key_prefix: str,
        payload: object,
        request_fingerprint: str | None,
    ) -> None:
        del step_key_prefix, payload, request_fingerprint
        raise RuntimeError("replay graph observer cannot persist external results")

    async def finalize(self, result: object) -> None:
        del result

    def compare_interrupt(self, actual: object | None) -> None:
        if actual is None:
            return
        payload = self._cursor.consume(ReplayStepKind.INTERRUPT_RESULT)
        if not isinstance(payload, InterruptResultStep):
            return
        if payload.interrupt.model_dump(mode="json") != getattr(
            actual, "model_dump", lambda **_: {}
        )(mode="json"):
            self._cursor.mismatches.append(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.INTERRUPT_MISMATCH,
                    expected_summary=payload.interrupt.kind.value,
                    actual_summary=getattr(getattr(actual, "kind", None), "value", "unknown"),
                )
            )

    def finish(self, actual_state: ReplaySafeAgentState) -> ReplayComparisonResult:
        route_fingerprint = sha256_fingerprint({"nodes": self.node_path, "routes": self.routes})
        actual_fingerprint = state_fingerprint(actual_state)
        try:
            payload = self._cursor.consume(ReplayStepKind.FINAL_STATE)
        except ReplayTapeMiss:
            return ReplayComparisonResult(
                status=ReplayExecutionStatus.INCOMPLETE,
                node_path=tuple(self.node_path),
                route_fingerprint=route_fingerprint,
                state_fingerprint=actual_fingerprint,
                mismatches=tuple(self._cursor.mismatches),
                consumed_steps=self._cursor.consumed,
                total_steps=self._cursor.total,
            )
        if isinstance(payload, FinalStateStep):
            if payload.route_fingerprint != route_fingerprint:
                self._cursor.mismatches.append(
                    ReplayMismatch(
                        mismatch_type=ReplayMismatchType.ROUTE_DECISION_MISMATCH,
                        expected_summary=payload.route_fingerprint,
                        actual_summary=route_fingerprint,
                    )
                )
            if payload.state_fingerprint != actual_fingerprint:
                expected_state = payload.state.model_dump(mode="json")
                actual_payload = actual_state.model_dump(mode="json")
                changed_fields = sorted(
                    key
                    for key in expected_state.keys() | actual_payload.keys()
                    if expected_state.get(key) != actual_payload.get(key)
                )
                expected_snapshot = expected_state.get("active_ticket_snapshot")
                actual_snapshot = actual_payload.get("active_ticket_snapshot")
                if isinstance(expected_snapshot, dict) and isinstance(actual_snapshot, dict):
                    changed_fields.extend(
                        f"active_ticket_snapshot.{key}"
                        for key in sorted(expected_snapshot.keys() | actual_snapshot.keys())
                        if expected_snapshot.get(key) != actual_snapshot.get(key)
                    )
                self._cursor.mismatches.append(
                    ReplayMismatch(
                        mismatch_type=ReplayMismatchType.FINAL_STATE_MISMATCH,
                        expected_summary=payload.state_fingerprint,
                        actual_summary=(
                            f"{actual_fingerprint}; changed_fields=" + ",".join(changed_fields[:20])
                        ),
                    )
                )
        self._cursor.finish()
        status = (
            ReplayExecutionStatus.DIVERGED
            if self._cursor.mismatches
            else ReplayExecutionStatus.PASSED
        )
        return ReplayComparisonResult(
            status=status,
            node_path=tuple(self.node_path),
            route_fingerprint=route_fingerprint,
            state_fingerprint=actual_fingerprint,
            mismatches=tuple(self._cursor.mismatches),
            consumed_steps=self._cursor.consumed,
            total_steps=self._cursor.total,
        )

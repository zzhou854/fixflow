"""Deterministic replay over the formal graph with tape-only dependencies."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent.state import AgentState
from app.agent_runtime.context import NodeContext
from app.agent_runtime.execution_context import bind_execution_context
from app.agent_runtime.graph import build_agent_graph
from app.agent_runtime.models import INTERRUPT_ADAPTER, RunStatus
from app.agent_runtime.runtime_state import RuntimeGraphState
from app.domain.enums import WorkflowStage
from app.property_operations.contracts.properties import GetResidentPropertyRequest
from app.replay.capture import GRAPH_SCHEMA_VERSION, REPLAY_BUNDLE_SCHEMA_VERSION
from app.replay.comparison import ReplayGraphObserver
from app.replay.enums import ReplayExecutionStatus, ReplayMismatchType, ReplayStepKind
from app.replay.models import (
    MessageReplayInput,
    ProvideInformationReplayInput,
    ReplayBundleView,
    ReplayComparisonResult,
    ReplayMismatch,
    SelectDuplicateReplayInput,
    SelectSlotReplayInput,
    ThreadCreatedReplayInput,
)
from app.replay.recorded import (
    RecordedComposeNode,
    RecordedInterpretationNode,
    RecordedPolicyService,
    RecordedPropertyOperationsClient,
)
from app.replay.repository import ReplayRepository
from app.replay.state import project_agent_state, restore_agent_state
from app.replay.steps import OperatorMutationResultStep
from app.replay.tape import ReplayTapeCursor, ReplayTapeMiss


class ReplayEngine:
    def __init__(
        self,
        repository: ReplayRepository,
        *,
        schema_version: int = REPLAY_BUNDLE_SCHEMA_VERSION,
        graph_schema_version: int = GRAPH_SCHEMA_VERSION,
    ) -> None:
        self._repository = repository
        self._schema_version = schema_version
        self._graph_schema_version = graph_schema_version

    async def execute(
        self, bundle: ReplayBundleView, *, execution_id: UUID
    ) -> ReplayComparisonResult:
        steps = await self._repository.load_steps(bundle.bundle_id)
        if (
            bundle.schema_version != self._schema_version
            or bundle.graph_schema_version != self._graph_schema_version
        ):
            return self._unsupported(len(steps))
        if not await self._repository.validate_integrity(bundle, steps):
            await self._repository.invalidate_bundle(
                bundle.bundle_id,
                error_code="BUNDLE_CHECKSUM_MISMATCH",
                occurred_at=bundle.finalized_at or bundle.captured_at,
            )
            return self._checksum_failure(len(steps))
        if bundle.start_state is None:
            return self._incomplete("REPLAY_START_STATE_MISSING", len(steps))
        cursor = ReplayTapeCursor(steps)
        if bundle.trigger_type.value == "OPERATOR_ACTION":
            return self._execute_operator(bundle, cursor)
        return await self._execute_graph(bundle, execution_id, cursor)

    async def _execute_graph(
        self,
        bundle: ReplayBundleView,
        execution_id: UUID,
        cursor: ReplayTapeCursor,
    ) -> ReplayComparisonResult:
        content_hash = self._content_hash(bundle)
        dependencies = NodeContext(
            mcp=RecordedPropertyOperationsClient(cursor),
            interpret=RecordedInterpretationNode(cursor, content_hash),
            compose=RecordedComposeNode(),
            retrieve_policy=RecordedPolicyService(cursor),
        )
        graph = build_agent_graph(dependencies, checkpointer=MemorySaver())
        observer = ReplayGraphObserver(cursor)
        config = RunnableConfig(configurable={"thread_id": f"replay:{execution_id}"})
        assert bundle.start_state is not None
        state = restore_agent_state(bundle.start_state)
        try:
            output = await self._invoke_graph(
                graph, config, state, bundle, dependencies, observer, cursor
            )
        except ReplayTapeMiss:
            return self._from_cursor(
                ReplayExecutionStatus.INCOMPLETE, observer, cursor, bundle.start_state
            )
        except Exception as exc:
            return self._failed_safe(type(exc).__name__, observer, cursor, bundle.start_state)
        return await self._compare_graph_output(graph, config, output, observer, cursor, bundle)

    async def _invoke_graph(
        self,
        graph: Any,
        config: RunnableConfig,
        state: AgentState,
        bundle: ReplayBundleView,
        dependencies: NodeContext,
        observer: ReplayGraphObserver,
        cursor: ReplayTapeCursor,
    ) -> object:
        if (
            cursor.next_kind is ReplayStepKind.PROPERTY_AUTHORIZATION_RESULT
            and state.property_id is not None
        ):
            await dependencies.mcp.get_resident_property(
                GetResidentPropertyRequest(
                    actor_type=state.actor_type,
                    actor_id=state.actor_id,
                    trace_id=bundle.original_trace_id,
                    resident_id=state.user_id,
                    property_id=state.property_id,
                )
            )
        if bundle.trigger_type.value == "RESUME":
            await self._prime_resume_graph(graph, config, state, bundle)
        with bind_execution_context(
            bundle.original_run_id,
            state.thread_id,
            bundle.original_trace_id,
            None,
            observer,
        ):
            if bundle.trigger_type.value == "RESUME":
                command: Command[object] = Command(
                    resume=self._resume_payload(bundle.input_envelope, bundle)
                )
                return await graph.ainvoke(command, config)
            return await graph.ainvoke(
                RuntimeGraphState(state_json=state.model_dump_json()), config
            )

    async def _compare_graph_output(
        self,
        graph: Any,
        config: RunnableConfig,
        output: object,
        observer: ReplayGraphObserver,
        cursor: ReplayTapeCursor,
        bundle: ReplayBundleView,
    ) -> ReplayComparisonResult:
        snapshot = await graph.aget_state(config)
        interrupt = None
        for task in snapshot.tasks:
            if task.interrupts:
                interrupt = INTERRUPT_ADAPTER.validate_python(task.interrupts[0].value)
                break
        if interrupt is not None:
            try:
                observer.compare_interrupt(interrupt)
            except ReplayTapeMiss:
                return self._from_cursor(
                    ReplayExecutionStatus.INCOMPLETE, observer, cursor, bundle.start_state
                )
        if not isinstance(output, dict) or "state_json" not in output:
            return self._failed_safe(
                "REPLAY_GRAPH_OUTPUT_INVALID", observer, cursor, bundle.start_state
            )
        actual_state = AgentState.model_validate_json(output["state_json"])
        actual = project_agent_state(
            actual_state,
            run_status=(
                RunStatus.INTERRUPTED.value
                if interrupt is not None
                else (
                    RunStatus.NEEDS_HUMAN_REVIEW.value
                    if actual_state.workflow_stage
                    in {WorkflowStage.HUMAN_REVIEW, WorkflowStage.EMERGENCY_REVIEW}
                    else RunStatus.COMPLETED.value
                )
            ),
        )
        return observer.finish(actual)

    async def _prime_resume_graph(
        self,
        graph: Any,
        config: RunnableConfig,
        state: AgentState,
        bundle: ReplayBundleView,
    ) -> None:
        input_envelope = bundle.input_envelope
        predecessor = {
            "PROVIDE_INFORMATION": "interpret",
            "SELECT_DUPLICATE_TICKET": "find_duplicates",
            "SELECT_APPOINTMENT_SLOT": "list_slots",
        }[input_envelope.kind]
        runtime = RuntimeGraphState(state_json=state.model_dump_json())
        await graph.aupdate_state(config, runtime, as_node=predecessor)
        await graph.ainvoke(None, config)

    @staticmethod
    def _resume_payload(input_envelope: object, bundle: ReplayBundleView) -> dict[str, object]:
        trace_id = bundle.original_trace_id
        if isinstance(input_envelope, ProvideInformationReplayInput):
            return {
                "kind": input_envelope.kind,
                "intent_version": input_envelope.intent_version,
                "user_message": "[REDACTED FOR REPLAY]",
                "trace_id": str(trace_id),
                "reference_time": input_envelope.reference_time.isoformat(),
                "timezone_name": input_envelope.timezone_name,
            }
        if isinstance(input_envelope, SelectDuplicateReplayInput):
            return {
                "kind": input_envelope.kind,
                "intent_version": input_envelope.intent_version,
                "candidates_fingerprint": input_envelope.candidate_fingerprint,
                "ticket_id": str(input_envelope.ticket_id),
                "trace_id": str(trace_id),
            }
        if isinstance(input_envelope, SelectSlotReplayInput):
            return {
                "kind": input_envelope.kind,
                "intent_version": input_envelope.intent_version,
                "candidates_fingerprint": input_envelope.candidate_fingerprint,
                "rank": input_envelope.rank,
                "trace_id": str(trace_id),
            }
        raise ValueError("invalid resume replay input")

    @staticmethod
    def _content_hash(bundle: ReplayBundleView) -> str:
        envelope = bundle.input_envelope
        if isinstance(
            envelope,
            (
                ThreadCreatedReplayInput,
                MessageReplayInput,
                ProvideInformationReplayInput,
            ),
        ):
            return envelope.message.content_hash
        return "0" * 64

    @staticmethod
    def _execute_operator(
        bundle: ReplayBundleView, cursor: ReplayTapeCursor
    ) -> ReplayComparisonResult:
        payload = cursor.consume(ReplayStepKind.OPERATOR_MUTATION_RESULT)
        if not isinstance(payload, OperatorMutationResultStep):
            return ReplayEngine._incomplete("OPERATOR_MUTATION_STEP_INVALID", cursor.total)
        final = cursor.consume(ReplayStepKind.FINAL_STATE)
        cursor.finish()
        status = (
            ReplayExecutionStatus.DIVERGED if cursor.mismatches else ReplayExecutionStatus.PASSED
        )
        return ReplayComparisonResult(
            status=status,
            node_path=(),
            route_fingerprint=getattr(final, "route_fingerprint", None),
            state_fingerprint=getattr(final, "state_fingerprint", None),
            mismatches=tuple(cursor.mismatches),
            consumed_steps=cursor.consumed,
            total_steps=cursor.total,
        )

    @staticmethod
    def _unsupported(total: int) -> ReplayComparisonResult:
        return ReplayComparisonResult(
            status=ReplayExecutionStatus.UNSUPPORTED_SCHEMA,
            node_path=(),
            mismatches=(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.SCHEMA_VERSION_MISMATCH,
                    expected_summary="supported schema",
                    actual_summary="unsupported schema",
                ),
            ),
            consumed_steps=0,
            total_steps=total,
        )

    @staticmethod
    def _checksum_failure(total: int) -> ReplayComparisonResult:
        return ReplayComparisonResult(
            status=ReplayExecutionStatus.FAILED_SAFE,
            node_path=(),
            mismatches=(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.BUNDLE_CHECKSUM_MISMATCH,
                    expected_summary="valid bundle checksum",
                    actual_summary="integrity validation failed",
                ),
            ),
            consumed_steps=0,
            total_steps=total,
        )

    @staticmethod
    def _incomplete(code: str, total: int) -> ReplayComparisonResult:
        return ReplayComparisonResult(
            status=ReplayExecutionStatus.INCOMPLETE,
            node_path=(),
            mismatches=(
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.REPLAY_TAPE_MISS,
                    actual_summary=code,
                ),
            ),
            consumed_steps=0,
            total_steps=total,
        )

    @staticmethod
    def _from_cursor(
        status: ReplayExecutionStatus,
        observer: ReplayGraphObserver,
        cursor: ReplayTapeCursor,
        fallback: object,
    ) -> ReplayComparisonResult:
        del fallback
        return ReplayComparisonResult(
            status=status,
            node_path=tuple(observer.node_path),
            mismatches=tuple(cursor.mismatches),
            consumed_steps=cursor.consumed,
            total_steps=cursor.total,
        )

    @staticmethod
    def _failed_safe(
        code: str,
        observer: ReplayGraphObserver,
        cursor: ReplayTapeCursor,
        fallback: object,
    ) -> ReplayComparisonResult:
        del fallback
        return ReplayComparisonResult(
            status=ReplayExecutionStatus.FAILED_SAFE,
            node_path=tuple(observer.node_path),
            mismatches=(
                *cursor.mismatches,
                ReplayMismatch(
                    mismatch_type=ReplayMismatchType.UNEXPECTED_REPLAY_CALL,
                    actual_summary=code,
                ),
            ),
            consumed_steps=cursor.consumed,
            total_steps=cursor.total,
        )

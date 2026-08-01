"""Trusted development-account routing for online language-only nodes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from app.agent.enums import AgentIntent
from app.agent.models import (
    ComposeResponseInput,
    ComposeResponseResult,
    InterpretationNodeResult,
    InterpretMessageInput,
    NodeMetadata,
)
from app.agent_runtime.execution_context import current_execution_context
from app.application.agent_reliability_models import RequiredUserAction
from app.domain.enums import WorkflowStage
from app.infrastructure.database.models.observability import TraceSource
from app.llm.online.grounded import (
    GroundedFact,
    GroundedResponseProvider,
    GroundedResponseRequest,
)
from app.trace.models import TracePayload

InterpretationCallable = Callable[[InterpretMessageInput], Awaitable[InterpretationNodeResult]]
ComposeCallable = Callable[[ComposeResponseInput], Awaitable[ComposeResponseResult]]


class AllowlistedInterpretationNode:
    """Choose online interpretation only from the trusted authenticated context."""

    def __init__(
        self,
        *,
        scripted: InterpretationCallable,
        online: InterpretationCallable,
        allowed_user_ids: frozenset[UUID],
        enabled: bool,
    ) -> None:
        self._scripted = scripted
        self._online = online
        self._allowed_user_ids = allowed_user_ids
        self._enabled = enabled

    async def __call__(self, node_input: InterpretMessageInput) -> InterpretationNodeResult:
        target = self._online if self._online_allowed() else self._scripted
        return await target(node_input)

    def _online_allowed(self) -> bool:
        context = current_execution_context()
        return bool(
            self._enabled
            and context is not None
            and context.user_id is not None
            and context.user_id in self._allowed_user_ids
        )

    async def close(self) -> None:
        close = getattr(self._online, "close", None)
        if close is not None:
            await close()


class AllowlistedComposeNode:
    """Choose online grounded presentation without changing business facts."""

    def __init__(
        self,
        *,
        scripted: ComposeCallable,
        online: ComposeCallable,
        allowed_user_ids: frozenset[UUID],
        enabled: bool,
    ) -> None:
        self._scripted = scripted
        self._online = online
        self._allowed_user_ids = allowed_user_ids
        self._enabled = enabled

    async def __call__(self, node_input: ComposeResponseInput) -> ComposeResponseResult:
        context = current_execution_context()
        online = bool(
            self._enabled
            and context is not None
            and context.user_id is not None
            and context.user_id in self._allowed_user_ids
        )
        return await (self._online if online else self._scripted)(node_input)

    async def close(self) -> None:
        close = getattr(self._online, "close", None)
        if close is not None:
            await close()


class OnlineGroundedComposeNode:
    """Adapt verified graph facts to the presentation-only grounded provider."""

    def __init__(self, provider: GroundedResponseProvider, *, model: str) -> None:
        self._provider = provider
        self._model = model

    async def __call__(self, node_input: ComposeResponseInput) -> ComposeResponseResult:
        template_id, action, display_action = _presentation_contract(node_input)
        facts = tuple(
            GroundedFact(fact_id=f"business.fact.{index}", safe_text=fact.statement)
            for index, fact in enumerate(node_input.verified_business_facts, start=1)
        )
        result = await self._provider.compose(
            GroundedResponseRequest(
                template_id=template_id,
                message_outcome="COMPLETED",
                required_user_action=action,
                display_action_text=display_action,
                facts=facts,
            )
        )
        context = current_execution_context()
        if context is not None and context.trace is not None:
            try:
                await context.trace.append_event(
                    event_key=context.trace.event_key(
                        context.run_id,
                        "grounded_response_completed",
                    ),
                    run_id=context.run_id,
                    thread_id=context.thread_id,
                    trace_id=context.trace_id,
                    source=TraceSource.AGENT,
                    event_type="grounded_response_completed",
                    node_name="compose",
                    payload=TracePayload(
                        provider="deepseek" if result.used_model else None,
                        model=self._model if result.used_model else None,
                        success=True,
                        deterministic_template_fallback=not result.used_model,
                    ),
                    occurred_at=datetime.now(UTC),
                )
            except Exception:
                pass
        return ComposeResponseResult(
            response_text=result.text,
            metadata=NodeMetadata(
                provider="deepseek" if result.used_model else "fixflow-deterministic-template",
                model=self._model if result.used_model else "server-owned-template-v1",
                prompt_name="grounded_response",
                prompt_version="1.1.0",
            ),
        )

    async def close(self) -> None:
        await self._provider.close()


def _presentation_contract(
    node_input: ComposeResponseInput,
) -> tuple[str, RequiredUserAction, str | None]:
    if node_input.task_intent is AgentIntent.QUERY_TICKET_STATUS:
        return "STATUS_UPDATE", RequiredUserAction.NONE, None
    if node_input.current_workflow_stage is WorkflowStage.AWAITING_SLOT_CONFIRMATION:
        return (
            "APPOINTMENT_PENDING",
            RequiredUserAction.SELECT_SLOT,
            "请选择可用的上门时间",
        )
    if any(fact.fact_type == "active_appointment" for fact in node_input.verified_business_facts):
        return "APPOINTMENT_BOOKED", RequiredUserAction.NONE, None
    if any(fact.fact_type == "ticket_snapshot" for fact in node_input.verified_business_facts):
        return "TICKET_CREATED", RequiredUserAction.NONE, None
    return "GENERIC_UPDATE", RequiredUserAction.NONE, None

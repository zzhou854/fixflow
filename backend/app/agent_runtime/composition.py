"""Explicit lifecycle composition for the Task 8 runtime."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.models import InterpretationNodeResult, InterpretMessageInput
from app.agent.nodes.compose_response import ComposeResponseNode
from app.agent.nodes.interpret_message import InterpretMessageNode
from app.agent.ports import LLMProvider
from app.agent_runtime.checkpoint import open_postgres_checkpointer
from app.agent_runtime.context import RuntimeDependencies
from app.agent_runtime.graph import build_agent_graph
from app.agent_runtime.mcp.client import StreamableHttpPropertyOperationsClient
from app.agent_runtime.orchestration import AgentOrchestrator
from app.config import Settings
from app.infrastructure.database.policy_uow import SqlAlchemyPolicyUnitOfWork
from app.llm.sanitizer import InterpretationInputLimits
from app.policy.ports import EmbeddingProvider, PolicyUnitOfWork
from app.policy.retrieval import PolicyRetrievalService
from app.reconciliation.coordinator import UnknownCommitCoordinator
from app.reconciliation.repository import SqlAlchemyReconciliationRepository
from app.replay.recording import (
    RecordingInterpretationNode,
    RecordingPolicyService,
    RecordingPropertyOperationsClient,
)


@runtime_checkable
class AsyncClosableProvider(Protocol):
    async def close(self) -> None: ...


@asynccontextmanager
async def open_agent_orchestrator(
    settings: Settings,
    *,
    interpretation_provider: LLMProvider,
    response_provider: LLMProvider,
    embedding_provider: EmbeddingProvider,
    language_model_name: str,
    interpretation_node: Callable[[InterpretMessageInput], Awaitable[InterpretationNodeResult]]
    | None = None,
) -> AsyncIterator[AgentOrchestrator]:
    """Build one runtime without import-time connections or business-session leakage."""

    if settings.checkpoint_database_url is None:
        raise ValueError("CHECKPOINT_DATABASE_URL is required for Agent runtime")
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    def policy_uow_factory() -> PolicyUnitOfWork:
        return SqlAlchemyPolicyUnitOfWork(sessions)

    policy = PolicyRetrievalService(
        uow_factory=policy_uow_factory,
        embedding_provider=embedding_provider,
    )
    async with StreamableHttpPropertyOperationsClient(settings.property_operations_mcp_url) as mcp:
        recording_mcp = RecordingPropertyOperationsClient(mcp)
        async with open_postgres_checkpointer(
            settings.checkpoint_database_url.get_secret_value()
        ) as checkpointer:
            dependencies = RuntimeDependencies(
                mcp=recording_mcp,
                interpret=RecordingInterpretationNode(
                    interpretation_node
                    or InterpretMessageNode(
                        interpretation_provider,
                        model=language_model_name,
                        input_limits=InterpretationInputLimits(
                            max_input_characters=settings.llm_max_input_characters,
                            max_context_messages=settings.llm_max_context_messages,
                            max_message_characters=settings.llm_max_message_characters,
                        ),
                    )
                ),
                compose=ComposeResponseNode(response_provider, model=language_model_name),
                retrieve_policy=RecordingPolicyService(policy.retrieve),
            )
            graph = build_agent_graph(dependencies, checkpointer=checkpointer)
            try:
                yield AgentOrchestrator(
                    graph,
                    recording_mcp,
                    UnknownCommitCoordinator(SqlAlchemyReconciliationRepository(sessions)),
                )
            finally:
                if interpretation_node is not None and isinstance(
                    interpretation_node, AsyncClosableProvider
                ):
                    await interpretation_node.close()
                await engine.dispose()
                if isinstance(interpretation_provider, AsyncClosableProvider):
                    await interpretation_provider.close()

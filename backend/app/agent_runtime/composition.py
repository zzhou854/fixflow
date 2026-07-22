"""Explicit lifecycle composition for the Task 8 runtime."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
from app.policy.ports import EmbeddingProvider, PolicyUnitOfWork
from app.policy.retrieval import PolicyRetrievalService
from app.reconciliation.coordinator import UnknownCommitCoordinator
from app.reconciliation.repository import SqlAlchemyReconciliationRepository


@asynccontextmanager
async def open_agent_orchestrator(
    settings: Settings,
    *,
    llm_provider: LLMProvider,
    embedding_provider: EmbeddingProvider,
    language_model_name: str,
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
        async with open_postgres_checkpointer(
            settings.checkpoint_database_url.get_secret_value()
        ) as checkpointer:
            dependencies = RuntimeDependencies(
                mcp=mcp,
                interpret=InterpretMessageNode(llm_provider, model=language_model_name),
                compose=ComposeResponseNode(llm_provider, model=language_model_name),
                retrieve_policy=policy.retrieve,
            )
            graph = build_agent_graph(dependencies, checkpointer=checkpointer)
            try:
                yield AgentOrchestrator(
                    graph,
                    mcp,
                    UnknownCommitCoordinator(SqlAlchemyReconciliationRepository(sessions)),
                )
            finally:
                await engine.dispose()

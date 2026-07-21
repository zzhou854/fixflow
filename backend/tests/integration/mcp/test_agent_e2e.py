"""First complete natural-language repair flow across real Task 8 boundaries."""

import asyncio
import socket
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
import uvicorn
from app.agent.nodes.compose_response import ComposeResponseNode
from app.agent.nodes.interpret_message import InterpretMessageNode
from app.agent_runtime.checkpoint import open_postgres_checkpointer
from app.agent_runtime.context import RuntimeDependencies
from app.agent_runtime.graph import build_agent_graph
from app.agent_runtime.mcp.client import StreamableHttpPropertyOperationsClient
from app.agent_runtime.models import (
    AgentCallerContext,
    AgentTurnInput,
    AppointmentSlotSelectionInterrupt,
    RunStatus,
    SelectAppointmentSlotResume,
)
from app.agent_runtime.orchestration import AgentOrchestrator
from app.domain.enums import ActorType
from app.infrastructure.database.policy_uow import SqlAlchemyPolicyUnitOfWork
from app.policy.corpus import load_policy_corpus
from app.policy.import_service import PolicyImportService
from app.policy.retrieval import PolicyRetrievalService
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mcp_server.config import MCPSettings
from mcp_server.server import create_server
from tests.fakes.embedding import DeterministicEmbeddingProvider
from tests.fakes.llm import ScriptedLLMProvider


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _wait_started(server: uvicorn.Server) -> None:
    for _ in range(100):
        if server.started:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("MCP server did not start")


@pytest.mark.asyncio
async def test_real_natural_language_create_and_book_flow(
    mcp_env: Any,
    empty_database_url: str,
) -> None:
    policy_engine = create_async_engine(mcp_env.database_url)
    policy_sessions = async_sessionmaker(policy_engine, expire_on_commit=False)

    def policy_uow_factory() -> SqlAlchemyPolicyUnitOfWork:
        return SqlAlchemyPolicyUnitOfWork(policy_sessions)

    embedding = DeterministicEmbeddingProvider()
    importer = PolicyImportService(uow_factory=policy_uow_factory, embedding_provider=embedding)
    retriever = PolicyRetrievalService(uow_factory=policy_uow_factory, embedding_provider=embedding)
    corpus = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json"))
    for document in corpus.documents:
        await importer.import_document(document)

    port = _free_port()
    fastmcp = create_server(
        MCPSettings(host="127.0.0.1", port=port, database_url=mcp_env.database_url)
    )
    server = uvicorn.Server(
        uvicorn.Config(
            fastmcp.streamable_http_app(),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    server_task = asyncio.create_task(server.serve())
    checkpoint_url = (
        make_url(empty_database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    llm = ScriptedLLMProvider(
        structured=(
            {
                "utterance_intent": "NEW_REPAIR",
                "issue_category": "WATER_LEAK",
                "issue_location": "厨房水槽下",
                "issue_description_update": "厨房水槽下普通滴漏，需要管道维修和预约候选时间",
                "user_availability_windows": [
                    {
                        "starts_at": mcp_env.slot.isoformat(),
                        "ends_at": (mcp_env.slot + timedelta(hours=4)).isoformat(),
                    }
                ],
            },
        ),
        text=("工单和预约均已由业务系统确认。",),
    )
    thread_id = uuid4()
    try:
        await _wait_started(server)
        async with StreamableHttpPropertyOperationsClient(f"http://127.0.0.1:{port}/mcp") as client:
            async with open_postgres_checkpointer(checkpoint_url) as checkpointer:
                graph = build_agent_graph(
                    RuntimeDependencies(
                        mcp=client,
                        interpret=InterpretMessageNode(llm, model="scripted"),
                        compose=ComposeResponseNode(llm, model="scripted"),
                        retrieve_policy=retriever.retrieve,
                    ),
                    checkpointer=checkpointer,
                )
                orchestrator = AgentOrchestrator(graph, client)
                turn = AgentTurnInput(
                    thread_id=thread_id,
                    trace_id=uuid4(),
                    actor_type=ActorType.RESIDENT,
                    actor_id=mcp_env.resident_id,
                    user_id=mcp_env.resident_id,
                    property_id=mcp_env.property_id,
                    user_message="厨房水槽下漏水，希望后天上午维修",
                    reference_time=mcp_env.slot - timedelta(days=2),
                    timezone_name="UTC",
                )
                interrupted = await orchestrator.start_turn(turn)
                assert isinstance(interrupted.interrupt, AppointmentSlotSelectionInterrupt)
                final = await orchestrator.resume(
                    thread_id,
                    AgentCallerContext(
                        actor_type=ActorType.RESIDENT,
                        actor_id=mcp_env.resident_id,
                        user_id=mcp_env.resident_id,
                    ),
                    SelectAppointmentSlotResume(
                        kind="SELECT_APPOINTMENT_SLOT",
                        intent_version=1,
                        candidates_fingerprint=(interrupted.interrupt.candidates_fingerprint),
                        rank=1,
                        trace_id=uuid4(),
                    ),
                )
                assert final.run_status is RunStatus.COMPLETED
                assert final.active_ticket_id is not None
                assert final.active_appointment_id is not None

        connection = await asyncpg.connect(
            mcp_env.database_url.replace("postgresql+asyncpg://", "postgresql://")
        )
        try:
            ticket_count = await connection.fetchval(
                "SELECT count(*) FROM repair_tickets "
                "WHERE resident_id=$1 AND property_id=$2 AND issue_location=$3",
                mcp_env.resident_id,
                mcp_env.property_id,
                "厨房水槽下",
            )
            appointment_count = await connection.fetchval(
                "SELECT count(*) FROM appointments WHERE ticket_id=$1 AND status='BOOKED'",
                final.active_ticket_id,
            )
            ticket_state = await connection.fetchrow(
                "SELECT status, version FROM repair_tickets WHERE id=$1",
                final.active_ticket_id,
            )
            ticket_history_count = await connection.fetchval(
                "SELECT count(*) FROM ticket_status_history WHERE ticket_id=$1",
                final.active_ticket_id,
            )
            appointment_history_count = await connection.fetchval(
                "SELECT count(*) FROM appointment_status_history WHERE appointment_id=$1",
                final.active_appointment_id,
            )
            idempotency_count = await connection.fetchval(
                "SELECT count(*) FROM idempotency_records "
                "WHERE actor_id=$1 AND idempotency_key LIKE 'agent-%'",
                str(mcp_env.resident_id),
            )
            assert ticket_count == 1
            assert appointment_count == 1
            assert ticket_state is not None
            assert ticket_state["status"] == "SCHEDULED"
            assert ticket_state["version"] == 2
            assert ticket_history_count == 2
            assert appointment_history_count == 1
            assert idempotency_count == 2
        finally:
            await connection.close()
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
        await policy_engine.dispose()

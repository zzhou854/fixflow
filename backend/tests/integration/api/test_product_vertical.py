"""Real HTTP -> Agent -> MCP -> Application -> PostgreSQL vertical evidence."""

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import asyncpg
import pytest
import uvicorn
from app.agent.nodes.compose_response import ComposeResponseNode
from app.agent.nodes.interpret_message import InterpretMessageNode
from app.agent_runtime.checkpoint import open_postgres_checkpointer
from app.agent_runtime.context import RuntimeDependencies
from app.agent_runtime.graph import build_agent_graph
from app.agent_runtime.mcp.client import StreamableHttpPropertyOperationsClient
from app.agent_runtime.orchestration import AgentOrchestrator
from app.api.app import create_app
from app.api.demo_providers import DemoDeterministicEmbeddingProvider, DemoScriptedLLMProvider
from app.api.dependencies import ApiServices
from app.api.services.agent import AgentApiService
from app.api.services.idempotency import ApiIdempotencyStore
from app.api.services.operator import OperatorActionService
from app.api.services.operator_review import OperatorThreadReviewService
from app.api.services.operator_trace import OperatorTraceQueryService
from app.api.services.sse import SSEEventBus
from app.application.auth import AuthService
from app.infrastructure.database.auth_repository import SqlAlchemyAuthUserRepository
from app.infrastructure.database.models import AgentRun, PolicyChunk, PolicyDocument, User
from app.infrastructure.database.policy_uow import SqlAlchemyPolicyUnitOfWork
from app.outbox.consumer import TraceDomainEventProjector
from app.outbox.dispatcher import OutboxDispatcher
from app.outbox.repository import SqlAlchemyOutboxDispatchRepository
from app.policy.corpus import load_policy_corpus
from app.policy.import_service import PolicyImportService
from app.policy.retrieval import PolicyRetrievalService
from app.trace.runtime import TraceRuntime
from app.trace.sanitizer import TraceSanitizer
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mcp_server.config import MCPSettings
from mcp_server.server import create_server


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
async def test_real_login_agent_slot_resume_and_cross_resident_denial(
    mcp_env: Any,
    empty_database_url: str,
) -> None:
    password_hasher = PasswordHasher()
    async with mcp_env.sessions.begin() as session:
        await session.execute(
            update(User)
            .where(User.id == mcp_env.resident_id)
            .values(password_hash=password_hasher.hash("resident-pass"))
        )
        await session.execute(
            update(User)
            .where(User.id == mcp_env.other_resident_id)
            .values(password_hash=password_hasher.hash("other-pass"))
        )

    policy_engine = create_async_engine(mcp_env.database_url)
    policy_sessions = async_sessionmaker(policy_engine, expire_on_commit=False)

    def policy_uow_factory() -> SqlAlchemyPolicyUnitOfWork:
        return SqlAlchemyPolicyUnitOfWork(policy_sessions)

    embedding = DemoDeterministicEmbeddingProvider()
    importer = PolicyImportService(uow_factory=policy_uow_factory, embedding_provider=embedding)
    retriever = PolicyRetrievalService(uow_factory=policy_uow_factory, embedding_provider=embedding)
    for document in load_policy_corpus(Path("data/policies/fixflow_demo_policies.json")).documents:
        await importer.import_document(document)

    port = _free_port()
    fastmcp = create_server(
        MCPSettings(host="127.0.0.1", port=port, database_url=mcp_env.database_url)
    )
    server = uvicorn.Server(
        uvicorn.Config(
            fastmcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"
        )
    )
    server_task = asyncio.create_task(server.serve())
    checkpoint_url = (
        make_url(empty_database_url)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )
    llm = DemoScriptedLLMProvider()
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
                events = SSEEventBus()
                auth = AuthService(
                    SqlAlchemyAuthUserRepository(mcp_env.sessions),
                    jwt_secret="vertical-test-secret-longer-than-thirty-two-characters",
                    password_hasher=password_hasher,
                )
                trace = TraceRuntime(
                    mcp_env.sessions,
                    TraceSanitizer(max_payload_bytes=8192, max_string_length=1024),
                )
                operator_review = OperatorThreadReviewService(orchestrator)
                services = ApiServices(
                    auth=auth,
                    application=mcp_env.application,
                    orchestrator=orchestrator,
                    agent=AgentApiService(orchestrator, mcp_env.application, events, trace),
                    operator_actions=OperatorActionService(mcp_env.application, trace),
                    operator_review=operator_review,
                    idempotency=ApiIdempotencyStore(),
                    events=events,
                    runtime_mode="demo",
                    operator_trace=OperatorTraceQueryService(operator_review, trace),
                )
                app = create_app(services)
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as http:
                    login = await http.post(
                        "/api/v1/auth/login",
                        json={
                            "username": f"mcp-resident-{mcp_env.resident_id}",
                            "password": "resident-pass",
                        },
                    )
                    token = login.json()["access_token"]
                    started = await http.post(
                        "/api/v1/agent/threads",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Idempotency-Key": "vertical-create-thread",
                        },
                        json={
                            "property_id": str(mcp_env.property_id),
                            "initial_message": "厨房水槽下漏水，希望后天上午维修",
                            "reference_time": (mcp_env.slot - timedelta(days=2)).isoformat(),
                            "timezone_name": "UTC",
                        },
                    )
                    body = started.json()
                    assert started.status_code == 200
                    assert body["interrupt"] is not None, body["policy_status"]
                    assert body["interrupt"]["kind"] == "APPOINTMENT_SLOT_SELECTION"
                    started_replay = await http.post(
                        "/api/v1/agent/threads",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Idempotency-Key": "vertical-create-thread",
                        },
                        json={
                            "property_id": str(mcp_env.property_id),
                            "initial_message": "厨房水槽下漏水，希望后天上午维修",
                            "reference_time": (mcp_env.slot - timedelta(days=2)).isoformat(),
                            "timezone_name": "UTC",
                        },
                    )
                    assert started_replay.status_code == 200
                    assert started_replay.json() == body
                    resumed = await http.post(
                        f"/api/v1/agent/threads/{body['thread_id']}/resume",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Idempotency-Key": "vertical-resume-thread",
                        },
                        json={
                            "kind": "SELECT_APPOINTMENT_SLOT",
                            "intent_version": body["interrupt"]["intent_version"],
                            "candidates_fingerprint": body["interrupt"]["candidates_fingerprint"],
                            "rank": 1,
                        },
                    )
                    final = resumed.json()
                    assert resumed.status_code == 200
                    assert final["run_status"] == "COMPLETED"
                    assert final["active_ticket"]["ticket_status"] == "SCHEDULED"
                    assert final["active_appointment"]["status"] == "BOOKED"
                    resumed_replay = await http.post(
                        f"/api/v1/agent/threads/{body['thread_id']}/resume",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Idempotency-Key": "vertical-resume-thread",
                        },
                        json={
                            "kind": "SELECT_APPOINTMENT_SLOT",
                            "intent_version": body["interrupt"]["intent_version"],
                            "candidates_fingerprint": body["interrupt"]["candidates_fingerprint"],
                            "rank": 1,
                        },
                    )
                    assert resumed_replay.status_code == 200
                    assert resumed_replay.json() == final
                    async with mcp_env.sessions() as session:
                        run_count = await session.scalar(
                            select(func.count())
                            .select_from(AgentRun)
                            .where(AgentRun.thread_id == UUID(body["thread_id"]))
                        )
                        assert run_count == 2

                    dispatcher = OutboxDispatcher(
                        SqlAlchemyOutboxDispatchRepository(mcp_env.sessions),
                        TraceDomainEventProjector(trace),
                        worker_id="vertical-dispatcher",
                        lease_seconds=30,
                        batch_size=50,
                        max_attempts=3,
                        retry_base_seconds=1,
                        clock=lambda: datetime.now(UTC),
                    )
                    assert await dispatcher.dispatch_once() >= 3
                    assert await dispatcher.dispatch_once() == 0

                    other_login = await http.post(
                        "/api/v1/auth/login",
                        json={
                            "username": f"mcp-other-{mcp_env.other_resident_id}",
                            "password": "other-pass",
                        },
                    )
                    other = other_login.json()["access_token"]
                    thread_denied = await http.get(
                        f"/api/v1/agent/threads/{body['thread_id']}",
                        headers={"Authorization": f"Bearer {other}"},
                    )
                    ticket_denied = await http.get(
                        f"/api/v1/resident/tickets/{final['active_ticket']['ticket_id']}",
                        headers={"Authorization": f"Bearer {other}"},
                    )
                    sse_denied = await http.get(
                        f"/api/v1/agent/threads/{body['thread_id']}/events",
                        headers={"Authorization": f"Bearer {other}"},
                    )
                    assert thread_denied.status_code == ticket_denied.status_code == 403
                    assert sse_denied.status_code == 403
                    assert await events.subscriber_count(UUID(body["thread_id"])) == 0

                connection = await asyncpg.connect(
                    mcp_env.database_url.replace("postgresql+asyncpg://", "postgresql://")
                )
                try:
                    assert (
                        await connection.fetchval(
                            "SELECT count(*) FROM repair_tickets WHERE id=$1",
                            UUID(final["active_ticket"]["ticket_id"]),
                        )
                        == 1
                    )
                    assert (
                        await connection.fetchval(
                            "SELECT count(*) FROM agent_runs WHERE thread_id=$1",
                            UUID(body["thread_id"]),
                        )
                        == 2
                    )
                    assert (
                        await connection.fetchval(
                            "SELECT count(*) FROM outbox_events "
                            "WHERE thread_id=$1 AND status='DISPATCHED'",
                            UUID(body["thread_id"]),
                        )
                        >= 3
                    )
                    assert (
                        await connection.fetchval(
                            "SELECT count(*) FROM agent_trace_events "
                            "WHERE thread_id=$1 AND source='DOMAIN'",
                            UUID(body["thread_id"]),
                        )
                        >= 3
                    )
                    assert (
                        await connection.fetchval(
                            "SELECT count(*) FROM appointments WHERE id=$1 AND status='BOOKED'",
                            UUID(final["active_appointment"]["appointment_id"]),
                        )
                        == 1
                    )
                finally:
                    await connection.close()
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, timeout=10)
        async with policy_sessions.begin() as session:
            demo_documents = select(PolicyDocument.id).where(
                PolicyDocument.embedding_provider == "fixflow-demo"
            )
            await session.execute(
                delete(PolicyChunk).where(PolicyChunk.document_id.in_(demo_documents))
            )
            await session.execute(
                delete(PolicyDocument).where(PolicyDocument.embedding_provider == "fixflow-demo")
            )
        await policy_engine.dispose()

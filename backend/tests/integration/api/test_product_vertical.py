"""Real HTTP -> Agent -> MCP -> Application -> PostgreSQL vertical evidence."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

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
from app.api.services.operator_reconciliation import OperatorReconciliationService
from app.api.services.operator_replay import OperatorReplayService
from app.api.services.operator_review import OperatorThreadReviewService
from app.api.services.operator_trace import OperatorTraceQueryService
from app.api.services.sse import SSEEventBus
from app.application.agent_reliability import AgentReliabilityService
from app.application.auth import AuthService
from app.domain.enums import (
    AppointmentPurpose,
    AppointmentStatus,
    IssueCategory,
    Severity,
    TicketStatus,
)
from app.fault_injection import FaultAction, FaultInjector, FaultPoint
from app.fault_injection.injector import InjectedFault
from app.infrastructure.database.agent_reliability_uow import (
    SqlAlchemyAgentReliabilityUnitOfWork,
)
from app.infrastructure.database.auth_repository import SqlAlchemyAuthUserRepository
from app.infrastructure.database.models import (
    AgentRun,
    Appointment,
    IdempotencyRecord,
    OperationReconciliationCase,
    OutboxEvent,
    PolicyChunk,
    PolicyDocument,
    RepairTicket,
    TicketStatusHistory,
    User,
)
from app.infrastructure.database.models.reconciliation import (
    ReconciliationAction,
    ReconciliationStatus,
)
from app.infrastructure.database.policy_uow import SqlAlchemyPolicyUnitOfWork
from app.outbox.consumer import TraceDomainEventProjector
from app.outbox.dispatcher import OutboxDispatcher
from app.outbox.repository import SqlAlchemyOutboxDispatchRepository
from app.policy.corpus import load_policy_corpus
from app.policy.import_service import PolicyImportService
from app.policy.retrieval import PolicyRetrievalService
from app.reconciliation.coordinator import UnknownCommitCoordinator
from app.reconciliation.mcp_client import ReconciliationOutcomeMCPClient
from app.reconciliation.repository import SqlAlchemyReconciliationRepository
from app.reconciliation.worker import ReconciliationWorker
from app.replay.capture import ReplayCaptureService
from app.replay.engine import ReplayEngine
from app.replay.recording import (
    RecordingInterpretationNode,
    RecordingPolicyService,
    RecordingPropertyOperationsClient,
)
from app.replay.repository import ReplayRepository
from app.replay.service import ReplayVerificationService
from app.trace.runtime import TraceRuntime
from app.trace.sanitizer import TraceSanitizer
from argon2 import PasswordHasher
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql.ranges import Range
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mcp_server.config import MCPSettings
from mcp_server.server import create_server


class ArmableFaultInjector(FaultInjector):
    """Test-only one-shot fault wired into the real client and server boundaries."""

    def __init__(self) -> None:
        self._point: FaultPoint | None = None
        self.operation_id: UUID | None = None

    def arm(self, point: FaultPoint) -> None:
        self._point = point
        self.operation_id = None

    async def hit(self, operation_id: UUID, point: FaultPoint) -> None:
        if point is self._point:
            self._point = None
            self.operation_id = operation_id
            raise InjectedFault(point, FaultAction.CONTROLLED_EXCEPTION)


@dataclass(slots=True)
class ReconciliationVerticalEnvironment:
    http: AsyncClient
    sessions: Any
    resident_token: str
    operator_token: str
    resident_id: UUID
    operator_id: UUID
    property_id: UUID
    slot: datetime
    worker_ids: tuple[UUID, UUID]
    faults: ArmableFaultInjector
    worker: ReconciliationWorker


@asynccontextmanager
async def _open_reconciliation_vertical(
    mcp_env: Any, empty_database_url: str
) -> AsyncIterator[ReconciliationVerticalEnvironment]:
    password_hasher = PasswordHasher()
    async with mcp_env.sessions.begin() as session:
        await session.execute(
            update(User)
            .where(User.id == mcp_env.resident_id)
            .values(password_hash=password_hasher.hash("resident-pass"))
        )
        await session.execute(
            update(User)
            .where(User.id == mcp_env.operator_id)
            .values(password_hash=password_hasher.hash("operator-pass"))
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

    faults = ArmableFaultInjector()
    port = _free_port()
    fastmcp = create_server(
        MCPSettings(host="127.0.0.1", port=port, database_url=mcp_env.database_url),
        fault_injector=faults,
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
    stack = AsyncExitStack()
    try:
        await _wait_started(server)
        client = await stack.enter_async_context(
            StreamableHttpPropertyOperationsClient(
                f"http://127.0.0.1:{port}/mcp", fault_injector=faults
            )
        )
        outcome_client = await stack.enter_async_context(
            ReconciliationOutcomeMCPClient(f"http://127.0.0.1:{port}/mcp")
        )
        checkpointer = await stack.enter_async_context(open_postgres_checkpointer(checkpoint_url))
        llm = DemoScriptedLLMProvider()
        graph = build_agent_graph(
            RuntimeDependencies(
                mcp=client,
                interpret=InterpretMessageNode(llm, model="scripted"),
                compose=ComposeResponseNode(llm, model="scripted"),
                retrieve_policy=retriever.retrieve,
            ),
            checkpointer=checkpointer,
        )
        repository = SqlAlchemyReconciliationRepository(mcp_env.sessions)
        coordinator = UnknownCommitCoordinator(repository)
        orchestrator = AgentOrchestrator(graph, client, reconciliation=coordinator)
        trace = TraceRuntime(
            mcp_env.sessions,
            TraceSanitizer(max_payload_bytes=8192, max_string_length=1024),
        )
        events = SSEEventBus()
        auth = AuthService(
            SqlAlchemyAuthUserRepository(mcp_env.sessions),
            jwt_secret="vertical-test-secret-longer-than-thirty-two-characters",
            password_hasher=password_hasher,
        )
        review = OperatorThreadReviewService(orchestrator)
        reliability = AgentReliabilityService(
            lambda: SqlAlchemyAgentReliabilityUnitOfWork(mcp_env.sessions)
        )
        services = ApiServices(
            auth=auth,
            application=mcp_env.application,
            orchestrator=orchestrator,
            agent=AgentApiService(
                orchestrator,
                mcp_env.application,
                events,
                trace,
                reliability=reliability,
            ),
            operator_actions=OperatorActionService(
                mcp_env.application,
                trace,
                reconciliation=coordinator,
                fault_injector=faults,
            ),
            operator_review=review,
            idempotency=ApiIdempotencyStore(),
            events=events,
            runtime_mode="demo",
            operator_trace=OperatorTraceQueryService(review, trace),
            operator_reconciliation=OperatorReconciliationService(mcp_env.sessions),
            agent_reliability=reliability,
        )
        http = await stack.enter_async_context(
            AsyncClient(transport=ASGITransport(app=create_app(services)), base_url="http://test")
        )
        resident_login = await http.post(
            "/api/v1/auth/login",
            json={
                "username": f"mcp-resident-{mcp_env.resident_id}",
                "password": "resident-pass",
            },
        )
        operator_login = await http.post(
            "/api/v1/auth/login",
            json={
                "username": f"mcp-operator-{mcp_env.operator_id}",
                "password": "operator-pass",
            },
        )
        yield ReconciliationVerticalEnvironment(
            http=http,
            sessions=mcp_env.sessions,
            resident_token=resident_login.json()["access_token"],
            operator_token=operator_login.json()["access_token"],
            resident_id=mcp_env.resident_id,
            operator_id=mcp_env.operator_id,
            property_id=mcp_env.property_id,
            slot=mcp_env.slot,
            worker_ids=mcp_env.worker_ids,
            faults=faults,
            worker=ReconciliationWorker(
                repository,
                outcome_client,
                worker_id="vertical-reconciliation-worker",
                lease_seconds=5,
                max_attempts=3,
                retry_base_seconds=1,
            ),
        )
    finally:
        await stack.aclose()
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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _checkpoint_row_counts(database_url: str) -> tuple[int, ...]:
    connection = await asyncpg.connect(database_url)
    try:
        counts = []
        for table_name in (
            "checkpoints",
            "checkpoint_blobs",
            "checkpoint_writes",
            "checkpoint_migrations",
        ):
            counts.append(await connection.fetchval(f'SELECT count(*) FROM "{table_name}"'))
        return tuple(counts)
    finally:
        await connection.close()


async def _wait_started(server: uvicorn.Server) -> None:
    for _ in range(100):
        if server.started:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("MCP server did not start")


async def _start_unknown_create(
    env: ReconciliationVerticalEnvironment, *, idempotency_key: str
) -> dict[str, Any]:
    env.faults.arm(FaultPoint.AFTER_SERVER_COMMIT_BEFORE_RESPONSE)
    response = await env.http.post(
        "/api/v1/agent/threads",
        headers={
            "Authorization": f"Bearer {env.resident_token}",
            "Idempotency-Key": idempotency_key,
        },
        json={
            "property_id": str(env.property_id),
            "initial_message": "厨房水槽下漏水，希望后天上午维修",
            "reference_time": (env.slot - timedelta(days=2)).isoformat(),
            "timezone_name": "UTC",
        },
    )
    assert response.status_code == 202, response.text
    body: dict[str, Any] = response.json()
    assert body["error_code"] == "RECONCILIATION_PENDING"
    assert body["run_status"] == "FAILED_SAFE"
    assert body["workflow_stage"] == "RECONCILIATION_PENDING"
    assert body["reconciliation"]["retry_allowed"] is False
    return body


async def _create_ticket_for_operator(
    env: ReconciliationVerticalEnvironment, *, key: str
) -> tuple[UUID, int]:
    response = await env.http.post(
        "/api/v1/agent/threads",
        headers={
            "Authorization": f"Bearer {env.resident_token}",
            "Idempotency-Key": key,
        },
        json={
            "property_id": str(env.property_id),
            "initial_message": "厨房水槽下漏水，希望后天上午维修",
            "reference_time": (env.slot - timedelta(days=2)).isoformat(),
            "timezone_name": "UTC",
        },
    )
    assert response.status_code == 200, response.text
    ticket = response.json()["active_ticket"]
    assert ticket is not None
    return UUID(ticket["ticket_id"]), int(ticket["version"])


def _escalation_payload(version: int) -> dict[str, object]:
    return {
        "expected_version": version,
        "reason_code": "OPERATOR_REVIEW",
        "reason_text": "需要物业人员人工复核",
        "evidence": ["vertical-test"],
    }


async def _post_escalation(
    env: ReconciliationVerticalEnvironment,
    ticket_id: UUID,
    version: int,
    *,
    key: str,
) -> Response:
    return await env.http.post(
        f"/api/v1/operator/tickets/{ticket_id}/escalate",
        headers={
            "Authorization": f"Bearer {env.operator_token}",
            "Idempotency-Key": key,
        },
        json=_escalation_payload(version),
    )


async def _assert_unknown_create_committed(env: ReconciliationVerticalEnvironment) -> None:
    body = await _start_unknown_create(env, idempotency_key="unknown-create-committed")
    assert env.faults.operation_id is not None
    replay = await env.http.post(
        "/api/v1/agent/threads",
        headers={
            "Authorization": f"Bearer {env.resident_token}",
            "Idempotency-Key": "unknown-create-committed",
        },
        json={
            "property_id": str(env.property_id),
            "initial_message": "厨房水槽下漏水，希望后天上午维修",
            "reference_time": (env.slot - timedelta(days=2)).isoformat(),
            "timezone_name": "UTC",
        },
    )
    assert replay.status_code == 202
    assert replay.json() == body
    recheck = await env.http.post(
        f"/api/v1/operator/reconciliation/cases/{body['reconciliation']['case_id']}/recheck",
        headers={"Authorization": f"Bearer {env.operator_token}"},
    )
    assert recheck.status_code == 200
    assert recheck.json()["status"] == "PENDING"
    async with env.sessions() as session:
        case = await session.scalar(
            select(OperationReconciliationCase).where(
                OperationReconciliationCase.id == UUID(body["reconciliation"]["case_id"])
            )
        )
        assert case is not None
        assert case.operation_id == env.faults.operation_id
        assert case.status is ReconciliationStatus.PENDING
    assert await env.worker.run_once(now=datetime.now(UTC)) == 1

    refreshed = await env.http.get(
        f"/api/v1/agent/threads/{body['thread_id']}",
        headers={"Authorization": f"Bearer {env.resident_token}"},
    )
    assert refreshed.status_code == 200
    current = refreshed.json()
    assert current["active_ticket"] is not None
    assert current["reconciliation"] is None
    async with env.sessions() as session:
        case = await session.get(
            OperationReconciliationCase, UUID(body["reconciliation"]["case_id"])
        )
        assert case is not None and case.status is ReconciliationStatus.RESOLVED_COMMITTED
        assert (
            await session.scalar(
                select(func.count())
                .select_from(RepairTicket)
                .where(RepairTicket.id == UUID(current["active_ticket"]["ticket_id"]))
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.operation_id == env.faults.operation_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OperationReconciliationCase)
                .where(OperationReconciliationCase.operation_id == env.faults.operation_id)
            )
            == 1
        )


async def _assert_conflicting_evidence(env: ReconciliationVerticalEnvironment) -> None:
    body = await _start_unknown_create(env, idempotency_key="unknown-create-conflict")
    operation_id = env.faults.operation_id
    assert operation_id is not None
    async with env.sessions.begin() as session:
        await session.execute(delete(OutboxEvent).where(OutboxEvent.operation_id == operation_id))
    assert await env.worker.run_once(now=datetime.now(UTC)) == 1

    resident = await env.http.get(
        f"/api/v1/agent/threads/{body['thread_id']}",
        headers={"Authorization": f"Bearer {env.resident_token}"},
    )
    assert resident.status_code == 200
    resident_body = resident.json()
    assert resident_body["workflow_stage"] == "HUMAN_REVIEW"
    assert resident_body["reconciliation"]["status"] == "MANUAL_REVIEW"

    operator = await env.http.get(
        f"/api/v1/operator/reconciliation/cases/{body['reconciliation']['case_id']}",
        headers={"Authorization": f"Bearer {env.operator_token}"},
    )
    assert operator.status_code == 200
    projection = operator.json()
    assert projection["status"] == "MANUAL_REVIEW"
    assert projection["evidence_status"] == "INCONSISTENT"
    assert set(projection).isdisjoint(
        {"idempotency_key", "request_payload", "pending_operation", "checkpoint", "sql"}
    )
    listing = await env.http.get(
        "/api/v1/operator/reconciliation/cases",
        params={"status": "MANUAL_REVIEW", "operation": "CREATE_TICKET"},
        headers={"Authorization": f"Bearer {env.operator_token}"},
    )
    assert listing.status_code == 200
    assert any(item["case_id"] == projection["case_id"] for item in listing.json()["items"])
    assert (
        await env.http.post(
            f"/api/v1/operator/reconciliation/cases/{projection['case_id']}/recheck",
            headers={"Authorization": f"Bearer {env.operator_token}"},
        )
    ).status_code == 409


async def _assert_not_committed_reuses_original_operation(
    env: ReconciliationVerticalEnvironment,
) -> None:
    started = await env.http.post(
        "/api/v1/agent/threads",
        headers={
            "Authorization": f"Bearer {env.resident_token}",
            "Idempotency-Key": "not-committed-thread",
        },
        json={
            "property_id": str(env.property_id),
            "initial_message": "厨房水槽下漏水，希望后天上午维修",
            "reference_time": (env.slot - timedelta(days=2)).isoformat(),
            "timezone_name": "UTC",
        },
    )
    assert started.status_code == 200
    initial = started.json()
    assert initial["interrupt"]["kind"] == "APPOINTMENT_SLOT_SELECTION"
    candidate = initial["interrupt"]["slots"][0]
    blocker_ticket_id, blocker_appointment_id = uuid4(), uuid4()
    async with env.sessions.begin() as session:
        session.add(
            RepairTicket(
                id=blocker_ticket_id,
                resident_id=env.resident_id,
                property_id=env.property_id,
                issue_category=IssueCategory.WATER_LEAK,
                issue_location="temporary blocker",
                issue_description="fault harness overlap",
                severity=Severity.MEDIUM,
                status=TicketStatus.SCHEDULED,
            )
        )
        session.add(
            Appointment(
                id=blocker_appointment_id,
                ticket_id=blocker_ticket_id,
                worker_id=UUID(candidate["worker_id"]),
                purpose=AppointmentPurpose.INITIAL_REPAIR,
                status=AppointmentStatus.BOOKED,
                scheduled_range=Range(
                    datetime.fromisoformat(candidate["scheduled_start"]),
                    datetime.fromisoformat(candidate["scheduled_end"]),
                    bounds="[)",
                ),
            )
        )

    resume_payload = {
        "kind": "SELECT_APPOINTMENT_SLOT",
        "intent_version": initial["interrupt"]["intent_version"],
        "candidates_fingerprint": initial["interrupt"]["candidates_fingerprint"],
        "rank": 1,
    }
    env.faults.arm(FaultPoint.AFTER_MCP_SEND)
    uncertain = await env.http.post(
        f"/api/v1/agent/threads/{initial['thread_id']}/resume",
        headers={
            "Authorization": f"Bearer {env.resident_token}",
            "Idempotency-Key": "not-committed-first-resume",
        },
        json=resume_payload,
    )
    assert uncertain.status_code == 202, uncertain.text
    uncertain_body = uncertain.json()
    original_operation_id = env.faults.operation_id
    assert original_operation_id is not None
    assert await env.worker.run_once(now=datetime.now(UTC)) == 1
    async with env.sessions.begin() as session:
        await session.execute(delete(Appointment).where(Appointment.id == blocker_appointment_id))
        await session.execute(delete(RepairTicket).where(RepairTicket.id == blocker_ticket_id))

    state = await env.http.get(
        f"/api/v1/agent/threads/{initial['thread_id']}",
        headers={"Authorization": f"Bearer {env.resident_token}"},
    )
    assert state.status_code == 200
    assert state.json()["reconciliation"] is None
    retried = await env.http.post(
        f"/api/v1/agent/threads/{initial['thread_id']}/resume",
        headers={
            "Authorization": f"Bearer {env.resident_token}",
            "Idempotency-Key": "not-committed-second-resume",
        },
        json=resume_payload,
    )
    assert retried.status_code == 200, retried.text
    final = retried.json()
    assert final["active_appointment"]["status"] == "BOOKED"
    async with env.sessions() as session:
        case = await session.get(
            OperationReconciliationCase,
            UUID(uncertain_body["reconciliation"]["case_id"]),
        )
        assert case is not None
        assert case.status is ReconciliationStatus.RESOLVED_NOT_COMMITTED
        assert case.operation_id == original_operation_id
        assert (
            await session.scalar(
                select(func.count())
                .select_from(IdempotencyRecord)
                .where(IdempotencyRecord.operation_id == original_operation_id)
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Appointment)
                .where(Appointment.ticket_id == UUID(final["active_ticket"]["ticket_id"]))
            )
            == 1
        )


@pytest.mark.asyncio
async def test_unknown_create_commit_is_reconciled_without_duplicate_mutation(
    mcp_env: Any, empty_database_url: str
) -> None:
    async with _open_reconciliation_vertical(mcp_env, empty_database_url) as env:
        await _assert_unknown_create_committed(env)


@pytest.mark.asyncio
async def test_conflicting_evidence_routes_manual_and_is_safely_operator_visible(
    mcp_env: Any, empty_database_url: str
) -> None:
    async with _open_reconciliation_vertical(mcp_env, empty_database_url) as env:
        await _assert_conflicting_evidence(env)


@pytest.mark.asyncio
async def test_unknown_rolled_back_booking_reuses_original_operation_and_key(
    mcp_env: Any, empty_database_url: str
) -> None:
    async with _open_reconciliation_vertical(mcp_env, empty_database_url) as env:
        await _assert_not_committed_reuses_original_operation(env)


@pytest.mark.asyncio
async def test_operator_commit_then_response_loss_reconciles_without_duplicate_escalation(
    mcp_env: Any, empty_database_url: str
) -> None:
    async with _open_reconciliation_vertical(mcp_env, empty_database_url) as env:
        ticket_id, version = await _create_ticket_for_operator(env, key="operator-commit-ticket")
        env.faults.arm(FaultPoint.AFTER_COMMIT_BEFORE_RESULT)
        first = await _post_escalation(env, ticket_id, version, key="operator-commit-escalation")
        assert first.status_code == 202, first.text
        body = first.json()
        assert body["code"] == "RECONCILIATION_PENDING"
        assert body["reconciliation"]["retry_allowed"] is False
        replay = await _post_escalation(env, ticket_id, version, key="operator-commit-escalation")
        assert replay.status_code == 202
        assert replay.json() == body
        assert await env.worker.run_once(now=datetime.now(UTC)) == 1

        async with env.sessions() as session:
            ticket = await session.get(RepairTicket, ticket_id)
            assert ticket is not None
            assert ticket.status is TicketStatus.ESCALATED
            assert ticket.version == version + 1
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TicketStatusHistory)
                    .where(
                        TicketStatusHistory.ticket_id == ticket_id,
                        TicketStatusHistory.to_status == TicketStatus.ESCALATED,
                    )
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(
                        OutboxEvent.aggregate_id == ticket_id,
                        OutboxEvent.event_type == "ticket.escalated",
                    )
                )
                == 1
            )
            case = await session.get(
                OperationReconciliationCase,
                UUID(body["reconciliation"]["case_id"]),
            )
            assert case is not None
            assert case.status is ReconciliationStatus.RESOLVED_COMMITTED


@pytest.mark.asyncio
async def test_operator_rolled_back_unknown_commit_retries_same_key_after_not_committed(
    mcp_env: Any, empty_database_url: str
) -> None:
    async with _open_reconciliation_vertical(mcp_env, empty_database_url) as env:
        ticket_id, version = await _create_ticket_for_operator(env, key="operator-rollback-ticket")
        trigger_name = f"reject_escalation_{ticket_id.hex}"
        function_name = f"reject_escalation_fn_{ticket_id.hex}"
        async with env.sessions.begin() as session:
            await session.execute(
                text(
                    f"CREATE FUNCTION {function_name}() RETURNS trigger LANGUAGE plpgsql AS $$ "
                    "BEGIN RAISE EXCEPTION 'forced rollback'; END $$"
                )
            )
            await session.execute(
                text(
                    f"CREATE TRIGGER {trigger_name} BEFORE UPDATE ON repair_tickets "
                    f"FOR EACH ROW WHEN (NEW.id = '{ticket_id}'::uuid "
                    "AND NEW.status = 'ESCALATED') "
                    f"EXECUTE FUNCTION {function_name}()"
                )
            )
        first = await _post_escalation(env, ticket_id, version, key="operator-rollback-escalation")
        assert first.status_code == 202, first.text
        body = first.json()
        assert await env.worker.run_once(now=datetime.now(UTC)) == 1
        async with env.sessions.begin() as session:
            case = await session.get(
                OperationReconciliationCase,
                UUID(body["reconciliation"]["case_id"]),
            )
            assert case is not None
            assert case.status is ReconciliationStatus.RESOLVED_NOT_COMMITTED
            await session.execute(text(f"DROP TRIGGER {trigger_name} ON repair_tickets"))
            await session.execute(text(f"DROP FUNCTION {function_name}()"))

        retried = await _post_escalation(
            env, ticket_id, version, key="operator-rollback-escalation"
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["code"] == "TICKET_ESCALATED"
        async with env.sessions() as session:
            ticket = await session.get(RepairTicket, ticket_id)
            assert ticket is not None
            assert ticket.status is TicketStatus.ESCALATED
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TicketStatusHistory)
                    .where(
                        TicketStatusHistory.ticket_id == ticket_id,
                        TicketStatusHistory.to_status == TicketStatus.ESCALATED,
                    )
                )
                == 1
            )


@pytest.mark.asyncio
async def test_resident_request_human_stops_without_operator_mutation(
    mcp_env: Any, empty_database_url: str
) -> None:
    async with _open_reconciliation_vertical(mcp_env, empty_database_url) as env:
        async with env.sessions() as session:
            tickets_before = await session.scalar(
                select(func.count())
                .select_from(RepairTicket)
                .where(RepairTicket.property_id == env.property_id)
            )
            escalations_before = await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type == "ticket.escalated")
            )
            cases_before = await session.scalar(
                select(func.count())
                .select_from(OperationReconciliationCase)
                .where(
                    OperationReconciliationCase.action == ReconciliationAction.ESCALATE_TO_OPERATOR
                )
            )
        response = await env.http.post(
            "/api/v1/agent/threads",
            headers={
                "Authorization": f"Bearer {env.resident_token}",
                "Idempotency-Key": "resident-human-only",
            },
            json={
                "property_id": str(env.property_id),
                "initial_message": "我要找人工处理",
                "reference_time": datetime.now(UTC).isoformat(),
                "timezone_name": "UTC",
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["workflow_stage"] == "HUMAN_REVIEW"
        assert body["active_ticket"] is None
        assert "需要物业工作人员人工处理" in body["assistant_message"]
        async with env.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(RepairTicket)
                    .where(RepairTicket.property_id == env.property_id)
                )
                == tickets_before
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OutboxEvent)
                    .where(OutboxEvent.event_type == "ticket.escalated")
                )
                == escalations_before
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(OperationReconciliationCase)
                    .where(
                        OperationReconciliationCase.action
                        == ReconciliationAction.ESCALATE_TO_OPERATOR
                    )
                )
                == cases_before
            )


@pytest.mark.asyncio
async def test_operator_conflicting_commit_evidence_routes_manual_without_redispatch(
    mcp_env: Any, empty_database_url: str
) -> None:
    async with _open_reconciliation_vertical(mcp_env, empty_database_url) as env:
        ticket_id, version = await _create_ticket_for_operator(env, key="operator-manual-ticket")
        env.faults.arm(FaultPoint.AFTER_COMMIT_BEFORE_RESULT)
        first = await _post_escalation(env, ticket_id, version, key="operator-manual-escalation")
        assert first.status_code == 202, first.text
        body = first.json()
        operation_id = env.faults.operation_id
        assert operation_id is not None
        async with env.sessions.begin() as session:
            await session.execute(
                delete(OutboxEvent).where(OutboxEvent.operation_id == operation_id)
            )
        assert await env.worker.run_once(now=datetime.now(UTC)) == 1

        case_response = await env.http.get(
            f"/api/v1/operator/reconciliation/cases/{body['reconciliation']['case_id']}",
            headers={"Authorization": f"Bearer {env.operator_token}"},
        )
        assert case_response.status_code == 200
        assert case_response.json()["status"] == "MANUAL_REVIEW"
        replay = await _post_escalation(env, ticket_id, version, key="operator-manual-escalation")
        assert replay.status_code == 202
        assert replay.json()["reconciliation"]["status"] == "MANUAL_REVIEW"
        async with env.sessions() as session:
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(TicketStatusHistory)
                    .where(
                        TicketStatusHistory.ticket_id == ticket_id,
                        TicketStatusHistory.to_status == TicketStatus.ESCALATED,
                    )
                )
                == 1
            )


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
        await session.execute(
            update(User)
            .where(User.id == mcp_env.operator_id)
            .values(password_hash=password_hasher.hash("operator-pass"))
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
                recording_client = RecordingPropertyOperationsClient(client)
                graph = build_agent_graph(
                    RuntimeDependencies(
                        mcp=recording_client,
                        interpret=RecordingInterpretationNode(
                            InterpretMessageNode(llm, model="scripted")
                        ),
                        compose=ComposeResponseNode(llm, model="scripted"),
                        retrieve_policy=RecordingPolicyService(retriever.retrieve),
                    ),
                    checkpointer=checkpointer,
                )
                orchestrator = AgentOrchestrator(graph, recording_client)
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
                replay_repository = ReplayRepository(mcp_env.sessions)
                replay_capture = ReplayCaptureService(
                    replay_repository, runtime_revision="vertical-test"
                )
                operator_trace = OperatorTraceQueryService(operator_review, trace)
                replay_verification = ReplayVerificationService(
                    replay_repository,
                    ReplayEngine(replay_repository),
                    runtime_revision="vertical-test",
                    graph_schema_version=1,
                    timeout_seconds=15,
                )
                reliability = AgentReliabilityService(
                    lambda: SqlAlchemyAgentReliabilityUnitOfWork(mcp_env.sessions)
                )
                services = ApiServices(
                    auth=auth,
                    application=mcp_env.application,
                    orchestrator=orchestrator,
                    agent=AgentApiService(
                        orchestrator,
                        mcp_env.application,
                        events,
                        trace,
                        replay=replay_capture,
                        reliability=reliability,
                    ),
                    operator_actions=OperatorActionService(
                        mcp_env.application, trace, replay=replay_capture
                    ),
                    operator_review=operator_review,
                    idempotency=ApiIdempotencyStore(),
                    events=events,
                    runtime_mode="demo",
                    operator_trace=operator_trace,
                    operator_replay=OperatorReplayService(
                        replay_repository,
                        replay_verification,
                        operator_trace,
                        trace,
                        mcp_env.application,
                    ),
                    agent_reliability=reliability,
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
                    assert final["message_outcome"] == "COMPLETED"
                    assert final["required_user_action"] == "NONE"
                    assert final["active_ticket"]["ticket_status"] == "SCHEDULED"
                    assert final["active_appointment"]["status"] == "BOOKED"
                    assert (
                        final["conversation_messages"][-1]["content"] == final["assistant_message"]
                    )
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
                    refreshed = await http.get(
                        f"/api/v1/agent/threads/{body['thread_id']}",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    assert refreshed.status_code == 200
                    assert (
                        refreshed.json()["conversation_messages"] == final["conversation_messages"]
                    )
                    recent = await http.get(
                        "/api/v1/agent/threads",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    assert recent.status_code == 200
                    summary = recent.json()["items"][0]
                    assert summary["thread_id"] == body["thread_id"]
                    archived = await http.post(
                        f"/api/v1/agent/threads/{body['thread_id']}/archive",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"expected_version": summary["version"]},
                    )
                    assert archived.status_code == 200
                    assert archived.json()["lifecycle_status"] == "ARCHIVED"
                    active_after_archive = await http.get(
                        "/api/v1/agent/threads",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    assert active_after_archive.json()["items"] == []
                    restored = await http.post(
                        f"/api/v1/agent/threads/{body['thread_id']}/restore",
                        headers={"Authorization": f"Bearer {token}"},
                        json={"expected_version": archived.json()["version"]},
                    )
                    assert restored.status_code == 200
                    assert restored.json()["lifecycle_status"] == "ACTIVE"

                    # The three frozen repair categories must all traverse the real
                    # HTTP -> graph -> MCP -> application -> PostgreSQL path.  They
                    # use distinct worker skills, so selecting the same ranked time
                    # does not weaken the scheduling-conflict assertions.
                    for category, message, request_key in (
                        (
                            IssueCategory.ELECTRICAL,
                            "客厅插座坏了，希望后天上午维修",
                            "vertical-electrical",
                        ),
                        (
                            IssueCategory.DOOR_LOCK,
                            "入户门锁坏了，希望后天上午维修",
                            "vertical-lock",
                        ),
                    ):
                        category_started = await http.post(
                            "/api/v1/agent/threads",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Idempotency-Key": f"{request_key}-create",
                            },
                            json={
                                "property_id": str(mcp_env.property_id),
                                "initial_message": message,
                                "reference_time": (mcp_env.slot - timedelta(days=2)).isoformat(),
                                "timezone_name": "UTC",
                            },
                        )
                        assert category_started.status_code == 200, category_started.text
                        category_body = category_started.json()
                        assert category_body["structured_issue"]["issue_category"] == category
                        assert category_body["interrupt"]["kind"] == "APPOINTMENT_SLOT_SELECTION"
                        category_resumed = await http.post(
                            f"/api/v1/agent/threads/{category_body['thread_id']}/resume",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Idempotency-Key": f"{request_key}-resume",
                            },
                            json={
                                "kind": "SELECT_APPOINTMENT_SLOT",
                                "intent_version": category_body["interrupt"]["intent_version"],
                                "candidates_fingerprint": category_body["interrupt"][
                                    "candidates_fingerprint"
                                ],
                                "rank": 1,
                            },
                        )
                        assert category_resumed.status_code == 200, category_resumed.text
                        category_final = category_resumed.json()
                        assert category_final["message_outcome"] == "COMPLETED"
                        assert category_final["active_ticket"]["issue_category"] == category
                        assert category_final["active_ticket"]["ticket_status"] == "SCHEDULED"
                        assert category_final["active_appointment"]["status"] == "BOOKED"
                        category_refreshed = await http.get(
                            f"/api/v1/agent/threads/{category_body['thread_id']}",
                            headers={"Authorization": f"Bearer {token}"},
                        )
                        assert category_refreshed.status_code == 200
                        assert (
                            category_refreshed.json()["conversation_messages"]
                            == category_final["conversation_messages"]
                        )

                    operator_login = await http.post(
                        "/api/v1/auth/login",
                        json={
                            "username": f"mcp-operator-{mcp_env.operator_id}",
                            "password": "operator-pass",
                        },
                    )
                    operator_token = operator_login.json()["access_token"]
                    replay_runs = await http.get(
                        f"/api/v1/operator/threads/{body['thread_id']}/replay-runs",
                        headers={"Authorization": f"Bearer {operator_token}"},
                    )
                    assert replay_runs.status_code == 200, replay_runs.text
                    replay_items = replay_runs.json()["items"]
                    assert len(replay_items) == 2
                    assert {item["bundle_status"] for item in replay_items} == {"READY"}
                    facts_before_replay = {}
                    async with mcp_env.sessions() as session:
                        for model in (
                            RepairTicket,
                            Appointment,
                            OutboxEvent,
                            OperationReconciliationCase,
                        ):
                            facts_before_replay[model.__tablename__] = await session.scalar(
                                select(func.count()).select_from(model)
                            )
                    checkpoint_rows_before_replay = await _checkpoint_row_counts(checkpoint_url)
                    replay_results = []
                    for item in replay_items:
                        verified = await http.post(
                            f"/api/v1/operator/runs/{item['run_id']}/replay/verify",
                            headers={
                                "Authorization": f"Bearer {operator_token}",
                                "Idempotency-Key": f"vertical-replay-{item['run_id']}",
                            },
                        )
                        assert verified.status_code == 200, verified.text
                        replay_results.append(verified.json())
                    assert {item["status"] for item in replay_results} == {"PASSED"}, replay_results
                    async with mcp_env.sessions() as session:
                        for model in (
                            RepairTicket,
                            Appointment,
                            OutboxEvent,
                            OperationReconciliationCase,
                        ):
                            assert (
                                await session.scalar(select(func.count()).select_from(model))
                                == facts_before_replay[model.__tablename__]
                            )
                    assert (
                        await _checkpoint_row_counts(checkpoint_url)
                        == checkpoint_rows_before_replay
                    )
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

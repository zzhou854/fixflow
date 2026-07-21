from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import pytest
from app.agent.enums import AgentIntent
from app.agent_runtime.models import RunStatus
from app.agent_runtime.orchestration import AgentOrchestrator
from app.api.app import create_app
from app.api.dependencies import ApiServices
from app.api.routers.resident_agent import stream_events
from app.api.schemas.agent import (
    AgentThreadResponse,
    OperatorThreadResponse,
    PolicyStatusResponse,
    StructuredIssueResponse,
)
from app.api.services.agent import AgentApiService
from app.api.services.idempotency import ApiIdempotencyStore
from app.api.services.operator import OperatorActionService
from app.api.services.operator_review import OperatorThreadReviewService
from app.api.services.sse import SSEEventBus
from app.application.auth import AuthenticatedIdentity, AuthService, AuthUser
from app.application.errors import AuthorizationFailed, PersistenceConflict, ResourceNotFound
from app.application.query_models import QueryActor, ResidentPropertyReadModel
from app.application.services import FixFlowApplicationService
from app.domain.enums import ActorType, WorkflowStage
from argon2 import PasswordHasher
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

SECRET = "integration-api-secret-longer-than-thirty-two-characters"


class FakeAuthRepository:
    def __init__(self, users: list[AuthUser]) -> None:
        self.users = users

    async def find_by_username(self, username: str) -> AuthUser | None:
        return next((user for user in self.users if user.username == username), None)

    async def find_by_id(self, user_id: UUID) -> AuthUser | None:
        return next((user for user in self.users if user.user_id == user_id), None)


class FakeApplication:
    def __init__(self, property_: ResidentPropertyReadModel) -> None:
        self.property = property_
        self.last_actor: QueryActor | None = None
        self.last_query: dict[str, object] = {}

    async def list_resident_properties(
        self, actor: QueryActor
    ) -> tuple[ResidentPropertyReadModel, ...]:
        self.last_actor = actor
        return (self.property,)

    async def list_resident_tickets(
        self, actor: QueryActor, *, limit: int, offset: int
    ) -> tuple[()]:
        self.last_actor = actor
        self.last_query = {"limit": limit, "offset": offset}
        return ()

    async def list_operator_tickets(self, actor: QueryActor, **kwargs: object) -> tuple[()]:
        self.last_actor = actor
        self.last_query = kwargs
        return ()

    async def get_ticket_detail(self, actor: QueryActor, ticket_id: UUID) -> None:
        self.last_actor = actor
        raise AuthorizationFailed("resident_not_authorized")


class FakeAgentApi:
    def __init__(self) -> None:
        self.identity: AuthenticatedIdentity | None = None
        self.calls: dict[str, int] = {"create": 0, "message": 0, "resume": 0}
        self.return_none = False

    @staticmethod
    def response(thread_id: UUID | None = None) -> AgentThreadResponse:
        return AgentThreadResponse(
            thread_id=thread_id or uuid4(),
            trace_id=uuid4(),
            workflow_stage=WorkflowStage.NEED_INFO,
            run_status=RunStatus.INTERRUPTED,
            assistant_message="请补充位置",
            interrupt=None,
            active_ticket=None,
            active_appointment=None,
            policy_status=PolicyStatusResponse(sufficiency=None, conflict=False, evidence_ids=()),
            structured_issue=StructuredIssueResponse(
                issue_category=None,
                issue_location=None,
                issue_description=None,
                severity=None,
            ),
            safety_review_required=False,
        )

    async def create_thread(
        self, identity: AuthenticatedIdentity, **kwargs: object
    ) -> AgentThreadResponse:
        self.identity = identity
        self.calls["create"] += 1
        return self.response()

    async def send_message(
        self, identity: AuthenticatedIdentity, *, thread_id: UUID, **kwargs: object
    ) -> AgentThreadResponse:
        self.identity = identity
        self.calls["message"] += 1
        return self.response(thread_id)

    async def resume(
        self, identity: AuthenticatedIdentity, *, thread_id: UUID, request: object
    ) -> AgentThreadResponse:
        self.identity = identity
        self.calls["resume"] += 1
        return self.response(thread_id)

    async def get_thread(
        self, identity: AuthenticatedIdentity, *, thread_id: UUID, trace_id: UUID
    ) -> AgentThreadResponse | None:
        self.identity = identity
        return None if self.return_none else self.response(thread_id)


class FakeOperatorActions:
    def __init__(self) -> None:
        self.last_call: dict[str, object] = {}
        self.call_count = 0
        self.error: Exception | None = None

    async def escalate(self, **kwargs: object) -> dict[str, object]:
        self.last_call = kwargs
        self.call_count += 1
        if self.error is not None:
            raise self.error
        return {
            "ok": True,
            "code": "ESCALATED",
            "resource_type": "ticket",
            "resource_id": kwargs["ticket_id"],
            "resource_version": 2,
            "replayed": False,
        }


class FakeOperatorReview:
    async def review(
        self, identity: AuthenticatedIdentity, **kwargs: object
    ) -> OperatorThreadResponse:
        thread_id = cast(UUID, kwargs["thread_id"])
        return OperatorThreadResponse(
            thread_id=thread_id,
            workflow_stage=WorkflowStage.HUMAN_REVIEW,
            run_status=RunStatus.NEEDS_HUMAN_REVIEW,
            task_intent=AgentIntent.NEW_REPAIR,
            issue_category=None,
            issue_location="厨房",
            severity=None,
            policy_sufficiency=None,
            policy_conflict=False,
            policy_evidence_summary=(),
            missing_fields=(),
            active_ticket_id=uuid4(),
            active_appointment_id=None,
            human_review_required=True,
            updated_at=datetime.now(UTC),
        )


ApiContext = tuple[
    FastAPI,
    AuthService,
    FakeApplication,
    FakeAgentApi,
    UUID,
    UUID,
    UUID,
]


@pytest.fixture
def api_context() -> ApiContext:
    hasher = PasswordHasher()
    resident_id, operator_id, property_id = uuid4(), uuid4(), uuid4()
    users = [
        AuthUser(resident_id, "resident", ActorType.RESIDENT, True, hasher.hash("resident-pass")),
        AuthUser(operator_id, "operator", ActorType.OPERATOR, True, hasher.hash("operator-pass")),
    ]
    auth = AuthService(FakeAuthRepository(users), jwt_secret=SECRET, password_hasher=hasher)
    application = FakeApplication(
        ResidentPropertyReadModel(
            resident_id=resident_id,
            property_id=property_id,
            community_name="星河花园",
            building_no="3",
            unit_no="2",
            room_no="1201",
            address_text="星河花园 1201",
        )
    )
    agent = FakeAgentApi()
    operator_actions = FakeOperatorActions()
    services = ApiServices(
        auth=auth,
        application=cast(FixFlowApplicationService, application),
        orchestrator=cast(AgentOrchestrator, object()),
        agent=cast(AgentApiService, agent),
        operator_actions=cast(OperatorActionService, operator_actions),
        operator_review=cast(OperatorThreadReviewService, FakeOperatorReview()),
        idempotency=ApiIdempotencyStore(),
        events=SSEEventBus(),
        runtime_mode="demo",
    )
    return create_app(services), auth, application, agent, resident_id, operator_id, property_id


async def _token(auth: AuthService, username: str, password: str) -> str:
    _, token = await auth.login(username, password)
    return token.token


def _mutation_headers(token: str, key: UUID | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": str(key or uuid4()),
    }


@pytest.mark.asyncio
async def test_login_and_me_return_only_safe_identity(api_context: ApiContext) -> None:
    app, _, _, _, resident_id, _, _ = api_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/login", json={"username": "resident", "password": "resident-pass"}
        )
        token = login.json()["access_token"]
        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert login.status_code == 200
    assert login.json()["user"]["user_id"] == str(resident_id)
    assert "password" not in str(login.json()).casefold()
    assert me.status_code == 200


@pytest.mark.asyncio
async def test_bad_password_and_missing_user_share_error(api_context: ApiContext) -> None:
    app, *_ = api_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/auth/login", json={"username": "resident", "password": "wrong"}
        )
        second = await client.post(
            "/api/v1/auth/login", json={"username": "missing", "password": "wrong"}
        )
    assert first.status_code == second.status_code == 401
    assert first.json()["code"] == second.json()["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_deactivated_account_is_rejected_by_me_and_protected_api(
    api_context: ApiContext,
) -> None:
    app, auth, *_ = api_context
    token = await _token(auth, "resident", "resident-pass")
    repository = cast(FakeAuthRepository, auth._repository)
    repository.users = [
        replace(user, is_active=False) if user.username == "resident" else user
        for user in repository.users
    ]
    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        me = await client.get("/api/v1/auth/me", headers=headers)
        protected = await client.get("/api/v1/resident/properties", headers=headers)
    assert me.status_code == protected.status_code == 401
    assert me.json()["code"] == protected.json()["code"] == "TOKEN_INVALID"


@pytest.mark.asyncio
async def test_jwt_identity_reaches_agent_and_body_cannot_override(
    api_context: ApiContext,
) -> None:
    app, auth, _, agent, resident_id, _, property_id = api_context
    token = await _token(auth, "resident", "resident-pass")
    body = {
        "property_id": str(property_id),
        "initial_message": "厨房漏水",
        "timezone_name": "Asia/Shanghai",
        "reference_time": datetime.now(UTC).isoformat(),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/threads", json=body, headers=_mutation_headers(token)
        )
        invalid = await client.post(
            "/api/v1/agent/threads",
            json={**body, "actor_id": str(uuid4())},
            headers=_mutation_headers(token),
        )
    assert response.status_code == 200
    assert agent.identity is not None and agent.identity.actor_id == resident_id
    assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_resident_property_query_uses_token_actor(api_context: ApiContext) -> None:
    app, auth, application, _, resident_id, _, property_id = api_context
    token = await _token(auth, "resident", "resident-pass")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/resident/properties", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    assert response.json()[0]["property_id"] == str(property_id)
    assert application.last_actor == QueryActor(ActorType.RESIDENT, resident_id)


@pytest.mark.asyncio
async def test_roles_are_separated_in_both_directions(api_context: ApiContext) -> None:
    app, auth, *_ = api_context
    resident = await _token(auth, "resident", "resident-pass")
    operator = await _token(auth, "operator", "operator-pass")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        operator_denied = await client.get(
            "/api/v1/operator/tickets", headers={"Authorization": f"Bearer {resident}"}
        )
        resident_denied = await client.get(
            "/api/v1/resident/properties", headers={"Authorization": f"Bearer {operator}"}
        )
    assert operator_denied.status_code == resident_denied.status_code == 403


@pytest.mark.asyncio
async def test_resume_union_rejects_wrong_kind(api_context: ApiContext) -> None:
    app, auth, *_ = api_context
    token = await _token(auth, "resident", "resident-pass")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/agent/threads/{uuid4()}/resume",
            json={"kind": "WRONG", "intent_version": 1},
            headers=_mutation_headers(token),
        )
    assert response.status_code == 409
    assert response.json()["code"] == "INTERRUPT_MISMATCH"


@pytest.mark.asyncio
async def test_sse_requires_authentication(api_context: ApiContext) -> None:
    app, *_ = api_context
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/agent/threads/{uuid4()}/events")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_resident_ticket_pagination_reaches_application(api_context: ApiContext) -> None:
    app, auth, application, *_ = api_context
    token = await _token(auth, "resident", "resident-pass")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/resident/tickets?limit=7&offset=3",
            headers=_mutation_headers(token),
        )
    assert response.status_code == 200
    assert response.json() == {"items": [], "limit": 7, "offset": 3}
    assert application.last_query == {"limit": 7, "offset": 3}


@pytest.mark.asyncio
async def test_operator_filters_are_typed_and_forwarded(api_context: ApiContext) -> None:
    app, auth, application, *_ = api_context
    token = await _token(auth, "operator", "operator-pass")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/operator/tickets?ticket_status=OPEN&issue_category=WATER_LEAK"
            "&severity=MEDIUM&limit=9&offset=2",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 200
    assert application.last_query["limit"] == 9
    assert application.last_query["offset"] == 2
    assert str(application.last_query["ticket_status"]) == "OPEN"
    assert str(application.last_query["issue_category"]) == "WATER_LEAK"
    assert str(application.last_query["severity"]) == "MEDIUM"


@pytest.mark.asyncio
async def test_operator_escalation_uses_token_identity(api_context: ApiContext) -> None:
    app, auth, _, _, _, operator_id, _ = api_context
    token = await _token(auth, "operator", "operator-pass")
    ticket_id = uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/operator/tickets/{ticket_id}/escalate",
            json={
                "expected_version": 1,
                "reason_code": "OPERATOR_REVIEW",
                "reason_text": "需要人工核查",
                "evidence": [],
            },
            headers=_mutation_headers(token),
        )
    actions = cast(FakeOperatorActions, app.state.services.operator_actions)
    assert response.status_code == 200
    assert actions.last_call["operator_id"] == operator_id
    assert actions.last_call["ticket_id"] == ticket_id


@pytest.mark.asyncio
async def test_operator_thread_review_is_role_protected_and_sanitized(
    api_context: ApiContext,
) -> None:
    app, auth, *_ = api_context
    operator = await _token(auth, "operator", "operator-pass")
    resident = await _token(auth, "resident", "resident-pass")
    path = f"/api/v1/operator/threads/{uuid4()}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        allowed = await client.get(path, headers={"Authorization": f"Bearer {operator}"})
        denied = await client.get(path, headers={"Authorization": f"Bearer {resident}"})
    assert allowed.status_code == 200
    assert denied.status_code == 403
    assert set(allowed.json()).isdisjoint(
        {
            "conversation_messages",
            "pending_operation",
            "checkpoint",
            "checkpoint_metadata",
            "idempotency_fingerprint",
            "assistant_message",
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [(ResourceNotFound("ticket_not_found"), 404), (PersistenceConflict("version_conflict"), 409)],
)
async def test_operator_escalation_maps_not_found_and_version_conflict(
    api_context: ApiContext, error: Exception, expected_status: int
) -> None:
    app, auth, *_ = api_context
    actions = cast(FakeOperatorActions, app.state.services.operator_actions)
    actions.error = error
    operator = await _token(auth, "operator", "operator-pass")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/operator/tickets/{uuid4()}/escalate",
            json={
                "expected_version": 1,
                "reason_code": "OPERATOR_REVIEW",
                "reason_text": "需要人工核查",
                "evidence": [],
            },
            headers=_mutation_headers(operator),
        )
    assert response.status_code == expected_status


@pytest.mark.asyncio
async def test_message_and_state_use_sanitized_thread_contract(api_context: ApiContext) -> None:
    app, auth, _, agent, resident_id, *_ = api_context
    token = await _token(auth, "resident", "resident-pass")
    thread_id = uuid4()
    headers = _mutation_headers(token)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sent = await client.post(
            f"/api/v1/agent/threads/{thread_id}/messages",
            json={
                "message": "补充厨房位置",
                "message_id": str(uuid4()),
                "timezone_name": "Asia/Shanghai",
                "reference_time": datetime.now(UTC).isoformat(),
            },
            headers=headers,
        )
        state = await client.get(f"/api/v1/agent/threads/{thread_id}", headers=headers)
    assert sent.status_code == state.status_code == 200
    assert agent.identity is not None and agent.identity.actor_id == resident_id
    assert set(state.json()).isdisjoint({"checkpoint", "provider", "sql", "mcp_response"})


@pytest.mark.asyncio
async def test_valid_information_resume_reaches_agent(api_context: ApiContext) -> None:
    app, auth, _, agent, resident_id, *_ = api_context
    token = await _token(auth, "resident", "resident-pass")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/agent/threads/{uuid4()}/resume",
            json={
                "kind": "PROVIDE_INFORMATION",
                "intent_version": 1,
                "user_message": "位置在厨房水槽下",
                "reference_time": datetime.now(UTC).isoformat(),
                "timezone_name": "Asia/Shanghai",
            },
            headers=_mutation_headers(token),
        )
    assert response.status_code == 200
    assert agent.identity is not None and agent.identity.actor_id == resident_id


@pytest.mark.asyncio
async def test_new_thread_without_property_stops_before_agent(api_context: ApiContext) -> None:
    app, auth, _, agent, *_ = api_context
    token = await _token(auth, "resident", "resident-pass")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/agent/threads",
            json={
                "initial_message": "厨房漏水",
                "timezone_name": "Asia/Shanghai",
                "reference_time": datetime.now(UTC).isoformat(),
            },
            headers=_mutation_headers(token),
        )
    assert response.status_code == 400
    assert response.json()["code"] == "PROPERTY_CONTEXT_REQUIRED"
    assert agent.identity is None


@pytest.mark.asyncio
async def test_create_thread_replay_returns_same_result_and_conflicting_payload_is_rejected(
    api_context: ApiContext,
) -> None:
    app, auth, _, agent, _, _, property_id = api_context
    token = await _token(auth, "resident", "resident-pass")
    headers = _mutation_headers(token, uuid4())
    body = {
        "property_id": str(property_id),
        "initial_message": "厨房漏水",
        "timezone_name": "Asia/Shanghai",
        "reference_time": datetime.now(UTC).isoformat(),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/agent/threads", json=body, headers=headers)
        replay = await client.post("/api/v1/agent/threads", json=body, headers=headers)
        conflict = await client.post(
            "/api/v1/agent/threads",
            json={**body, "initial_message": "门锁损坏"},
            headers=headers,
        )
    assert first.json() == replay.json()
    assert agent.calls["create"] == 1
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"


@pytest.mark.asyncio
async def test_message_resume_and_escalate_requests_replay_without_duplicate_calls(
    api_context: ApiContext,
) -> None:
    app, auth, _, agent, *_ = api_context
    resident = await _token(auth, "resident", "resident-pass")
    operator = await _token(auth, "operator", "operator-pass")
    thread_id, message_id, ticket_id = uuid4(), uuid4(), uuid4()
    message_body = {
        "message": "补充厨房位置",
        "message_id": str(message_id),
        "timezone_name": "Asia/Shanghai",
        "reference_time": datetime.now(UTC).isoformat(),
    }
    resume_body = {
        "kind": "PROVIDE_INFORMATION",
        "intent_version": 1,
        "user_message": "位置在厨房",
        "timezone_name": "Asia/Shanghai",
        "reference_time": datetime.now(UTC).isoformat(),
    }
    escalation_body = {
        "expected_version": 1,
        "reason_code": "OPERATOR_REVIEW",
        "reason_text": "需要人工核查",
        "evidence": [],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(2):
            await client.post(
                f"/api/v1/agent/threads/{thread_id}/messages",
                json=message_body,
                headers=_mutation_headers(resident, message_id),
            )
            await client.post(
                f"/api/v1/agent/threads/{thread_id}/resume",
                json=resume_body,
                headers=_mutation_headers(resident, thread_id),
            )
            await client.post(
                f"/api/v1/operator/tickets/{ticket_id}/escalate",
                json=escalation_body,
                headers=_mutation_headers(operator, ticket_id),
            )
    actions = cast(FakeOperatorActions, app.state.services.operator_actions)
    assert agent.calls["message"] == 1
    assert agent.calls["resume"] == 1
    assert actions.call_count == 1


@pytest.mark.asyncio
async def test_sse_url_token_is_ignored_and_auth_failure_registers_no_subscriber(
    api_context: ApiContext,
) -> None:
    app, auth, *_ = api_context
    token = await _token(auth, "resident", "resident-pass")
    thread_id = uuid4()
    events = app.state.services.events
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/agent/threads/{thread_id}/events?token={token}")
        invalid = await client.get(
            f"/api/v1/agent/threads/{thread_id}/events",
            headers={"Authorization": "Bearer invalid"},
        )
    assert response.status_code == invalid.status_code == 401
    assert await events.subscriber_count(thread_id) == 0


@pytest.mark.asyncio
async def test_sse_unknown_thread_uses_same_forbidden_boundary_before_subscribe(
    api_context: ApiContext,
) -> None:
    app, auth, _, agent, *_ = api_context
    agent.return_none = True
    token = await _token(auth, "resident", "resident-pass")
    thread_id = uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            f"/api/v1/agent/threads/{thread_id}/events",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "THREAD_IDENTITY_CONFLICT"
    assert await app.state.services.events.subscriber_count(thread_id) == 0


@pytest.mark.asyncio
async def test_cors_allows_configured_origin_and_omits_unknown_origin(
    api_context: ApiContext,
) -> None:
    app, *_ = api_context
    with pytest.raises(ValueError, match="explicit"):
        create_app(app.state.services, cors_origins="*")
    preflight = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization,Idempotency-Key",
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        allowed = await client.options(
            "/api/v1/auth/me", headers={**preflight, "Origin": "http://localhost:5173"}
        )
        unknown = await client.options(
            "/api/v1/auth/me", headers={**preflight, "Origin": "https://unknown.invalid"}
        )
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in unknown.headers


@pytest.mark.asyncio
async def test_asgi_lifespan_can_start_twice_without_sse_subscriber_residue(
    api_context: ApiContext,
) -> None:
    app, *_ = api_context
    thread_id = uuid4()
    for _ in range(2):
        async with app.router.lifespan_context(app):
            assert app.state.services is not None
    assert await app.state.services.events.subscriber_count(thread_id) == 0


@pytest.mark.asyncio
async def test_sse_heartbeat_is_safe_and_stream_close_removes_subscriber(
    api_context: ApiContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, auth, *_ = api_context
    identity, _ = await auth.login("resident", "resident-pass")
    thread_id = uuid4()

    class ConnectedRequest:
        async def is_disconnected(self) -> bool:
            return False

    async def immediate_timeout(awaitable: object, **kwargs: object) -> object:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise TimeoutError

    monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)
    response = await stream_events(
        thread_id,
        cast(Request, ConnectedRequest()),
        identity,
        app.state.services,
    )
    iterator = cast(AsyncGenerator[str, None], response.body_iterator)
    chunk = await anext(iterator)
    assert "event: heartbeat" in str(chunk)
    await iterator.aclose()
    assert await app.state.services.events.subscriber_count(thread_id) == 0

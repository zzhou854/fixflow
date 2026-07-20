"""Policy results merge only when the current intent and request still match."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.agent.enums import AgentIntent
from app.agent.state import AgentState
from app.domain.enums import ActorType, IssueCategory, WorkflowStage
from app.policy.enums import EvidenceSufficiency, PolicyTopic
from app.policy.errors import StalePolicyResult
from app.policy.models import EmbeddingProfile, PolicyRetrievalRequest, PolicyRetrievalResult
from app.policy.retrieval import policy_query_fingerprint
from app.policy.state_merge import merge_policy_result

NOW = datetime(2032, 1, 1, tzinfo=UTC)
PROFILE = EmbeddingProfile(
    provider="fixflow-test", model="character-bigram-hash", dimension=384, profile_version="v1"
)


def _state(
    *, intent_version: int = 3, category: IssueCategory = IssueCategory.WATER_LEAK
) -> AgentState:
    return AgentState(
        thread_id=uuid4(),
        trace_id=uuid4(),
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        user_id=uuid4(),
        property_id=uuid4(),
        active_ticket_id=uuid4(),
        task_intent=AgentIntent.QUERY_TICKET_STATUS,
        workflow_stage=WorkflowStage.POLICY_CHECK,
        ticket_snapshot_version=3,
        intent_version=intent_version,
        issue_category=category,
    )


def _request(
    *,
    intent_version: int = 3,
    category: IssueCategory = IssueCategory.WATER_LEAK,
    topics: tuple[PolicyTopic, ...] = (PolicyTopic.SAFETY_ESCALATION,),
    query: str = "漏水安全规则",
) -> PolicyRetrievalRequest:
    return PolicyRetrievalRequest(
        query_text=query,
        issue_category=category,
        policy_topics=topics,
        as_of=NOW,
        intent_version=intent_version,
        top_k=5,
        minimum_vector_similarity=0.2,
        minimum_fusion_score=0.25,
        trace_id=uuid4(),
    )


def _result(request: PolicyRetrievalRequest) -> PolicyRetrievalResult:
    return PolicyRetrievalResult(
        evidence=(),
        conflicts=(),
        sufficiency=EvidenceSufficiency.INSUFFICIENT,
        missing_policy_topics=request.policy_topics,
        retrieved_as_of=request.as_of,
        intent_version=request.intent_version,
        issue_category=request.issue_category,
        requested_policy_topics=request.policy_topics,
        embedding_profile=PROFILE,
        policy_query_fingerprint=policy_query_fingerprint(request, PROFILE),
    )


def test_policy_merge_updates_only_policy_metadata() -> None:
    state = _state()
    request = _request()
    merged = merge_policy_result(state, _result(request), request, PROFILE)
    assert merged.policy_sufficiency is EvidenceSufficiency.INSUFFICIENT
    assert merged.missing_policy_topics == request.policy_topics
    protected = (
        "actor_type",
        "actor_id",
        "user_id",
        "property_id",
        "active_ticket_id",
        "active_appointment_id",
        "severity",
        "workflow_stage",
        "pending_action",
        "user_confirmation",
        "ticket_snapshot_version",
        "appointment_version",
    )
    for field in protected:
        assert getattr(merged, field) == getattr(state, field)


@pytest.mark.parametrize("stale_kind", ["intent", "category", "topics", "fingerprint"])
def test_stale_policy_result_is_rejected_without_state_mutation(stale_kind: str) -> None:
    state = _state()
    request = _request()
    result = _result(request)
    current_request = request
    if stale_kind == "intent":
        state = _state(intent_version=4)
    elif stale_kind == "category":
        state = _state(category=IssueCategory.ELECTRICAL)
        current_request = _request(category=IssueCategory.ELECTRICAL)
    elif stale_kind == "topics":
        current_request = _request(topics=(PolicyTopic.REWORK,))
    else:
        result = result.model_copy(update={"policy_query_fingerprint": "f" * 64})
    before = state.model_dump(mode="python")
    with pytest.raises(StalePolicyResult):
        merge_policy_result(state, result, current_request, PROFILE)
    assert state.model_dump(mode="python") == before


def test_late_request_a_cannot_overwrite_newer_request_b_with_same_intent() -> None:
    state = _state()
    request_a = _request(query="漏水安全规则")
    request_b = _request(query="漏水返工规则", topics=(PolicyTopic.REWORK,))
    merged_b = merge_policy_result(state, _result(request_b), request_b, PROFILE)
    with pytest.raises(StalePolicyResult):
        merge_policy_result(merged_b, _result(request_a), request_b, PROFILE)


def test_result_from_old_embedding_profile_is_rejected() -> None:
    state = _state()
    request = _request()
    new_profile = PROFILE.model_copy(update={"model": "new-model"})
    with pytest.raises(StalePolicyResult):
        merge_policy_result(state, _result(request), request, new_profile)

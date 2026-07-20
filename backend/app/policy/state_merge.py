"""Narrow Agent State merge for deterministic policy results."""

from app.agent.state import AgentState
from app.policy.errors import StalePolicyResult
from app.policy.models import EmbeddingProfile, PolicyRetrievalRequest, PolicyRetrievalResult
from app.policy.retrieval import policy_query_fingerprint


def merge_policy_result(
    state: AgentState,
    result: PolicyRetrievalResult,
    request: PolicyRetrievalRequest,
    expected_embedding_profile: EmbeddingProfile,
) -> AgentState:
    """Merge only policy evidence metadata; never advance identity or workflow state."""

    expected = policy_query_fingerprint(request, expected_embedding_profile)
    if (
        state.issue_category != request.issue_category
        or state.intent_version != request.intent_version
        or result.intent_version != state.intent_version
        or result.issue_category != request.issue_category
        or set(result.requested_policy_topics) != set(request.policy_topics)
        or result.retrieved_as_of != request.as_of
        or result.embedding_profile != expected_embedding_profile
        or result.policy_query_fingerprint != expected
    ):
        raise StalePolicyResult("policy retrieval result no longer matches current state/request")

    return state.model_copy(
        update={
            "policy_evidence_ids": tuple(item.evidence_id for item in result.evidence),
            "policy_conflict": bool(result.conflicts),
            "policy_sufficiency": result.sufficiency,
            "missing_policy_topics": result.missing_policy_topics,
            "policy_retrieved_as_of": result.retrieved_as_of,
            "policy_query_fingerprint": result.policy_query_fingerprint,
        }
    )

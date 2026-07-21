"""Policy retrieval node and its strictly bounded state merge."""

from datetime import datetime
from typing import cast

from app.agent_runtime.context import NodeContext
from app.agent_runtime.errors import PolicyRetrievalFailed
from app.agent_runtime.runtime_state import RuntimeGraphState, dump_state, load_state
from app.domain.enums import WorkflowStage
from app.policy.enums import PolicyTopic
from app.policy.models import PolicyRetrievalRequest
from app.policy.state_merge import merge_policy_result


async def retrieve_policy(
    context: NodeContext, graph_state: RuntimeGraphState
) -> RuntimeGraphState:
    state = load_state(graph_state)
    assert state.issue_category is not None
    request = PolicyRetrievalRequest(
        query_text=f"{state.issue_category.value} {state.issue_description}",
        issue_category=state.issue_category,
        policy_topics=(PolicyTopic.RESPONSIBILITY_SCOPE, PolicyTopic.APPOINTMENT),
        as_of=cast(datetime, state.current_reference_time),
        intent_version=state.intent_version,
        trace_id=state.trace_id,
    )
    try:
        result = await context.retrieve_policy(request)
        merged = merge_policy_result(state, result, request, result.embedding_profile)
    except Exception as exc:
        # Retrieval failure is a fail-safe boundary.  In particular, stale or
        # malformed results cannot become an implicit "sufficient" decision.
        raise PolicyRetrievalFailed("policy retrieval could not be verified") from exc
    return dump_state(merged.model_copy(update={"workflow_stage": WorkflowStage.POLICY_CHECK}))

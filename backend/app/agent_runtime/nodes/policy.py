"""Policy retrieval node and its strictly bounded state merge."""

from datetime import datetime
from typing import cast

from app.agent_runtime.context import NodeContext
from app.agent_runtime.errors import PolicyRetrievalFailed
from app.agent_runtime.runtime_state import RuntimeGraphState, dump_state, load_state
from app.domain.enums import IssueCategory, WorkflowStage
from app.policy.enums import PolicyTopic
from app.policy.models import PolicyRetrievalRequest
from app.policy.state_merge import merge_policy_result

_POLICY_QUERY_CONTEXT = {
    PolicyTopic.RESPONSIBILITY_SCOPE: "故障维修 责任范围",
    PolicyTopic.APPOINTMENT: "候选时间 正式预约 预约确认",
}
_CATEGORY_QUERY_CONTEXT = {
    IssueCategory.WATER_LEAK: "普通漏水 滴漏 管道维修",
    IssueCategory.ELECTRICAL: "电气维修 插座 跳闸",
    IssueCategory.DOOR_LOCK: "非紧急换锁 预约锁匠",
}


async def retrieve_policy(
    context: NodeContext, graph_state: RuntimeGraphState
) -> RuntimeGraphState:
    state = load_state(graph_state)
    assert state.issue_category is not None
    topics = (PolicyTopic.RESPONSIBILITY_SCOPE, PolicyTopic.APPOINTMENT)
    query_context = " ".join(
        (
            *(_POLICY_QUERY_CONTEXT[topic] for topic in topics),
            _CATEGORY_QUERY_CONTEXT[state.issue_category],
        )
    )
    request = PolicyRetrievalRequest(
        query_text=(f"{state.issue_category.value} {state.issue_description} {query_context}"),
        issue_category=state.issue_category,
        policy_topics=topics,
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

"""Topology-only builder for FixFlow's single deterministic LangGraph."""

from collections.abc import Awaitable, Callable
from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent_runtime.context import NodeContext, RuntimeDependencies
from app.agent_runtime.nodes.escalation import escalate, prepare_escalate
from app.agent_runtime.nodes.interpretation import interpret_message
from app.agent_runtime.nodes.interrupts import need_information, select_duplicate, select_slot
from app.agent_runtime.nodes.policy import retrieve_policy
from app.agent_runtime.nodes.property import resolve_property
from app.agent_runtime.nodes.response import (
    compose,
    finish_policy_review,
    finish_safety,
    finish_unsupported,
)
from app.agent_runtime.nodes.scheduling import (
    book,
    list_slots,
    prepare_book,
    prepare_reschedule,
    reschedule,
)
from app.agent_runtime.nodes.tickets import (
    create_ticket,
    find_duplicates,
    prepare_create,
    refresh_snapshot,
    resolve_existing,
)
from app.agent_runtime.routing import (
    route_after_interpret,
    route_after_policy,
    route_after_property,
    route_after_snapshot,
    route_duplicates,
    route_resolved_existing,
)
from app.agent_runtime.runtime_state import RuntimeGraphState

__all__ = ["RuntimeGraphState", "build_agent_graph"]


def build_agent_graph(
    dependencies: RuntimeDependencies,
    *,
    checkpointer: BaseCheckpointSaver[str],
) -> CompiledStateGraph[RuntimeGraphState, None, RuntimeGraphState, RuntimeGraphState]:
    """Compile the fixed graph topology; node bodies live by responsibility."""

    context: NodeContext = dependencies
    graph = StateGraph(RuntimeGraphState)

    def add_node(
        name: str, node: Callable[[RuntimeGraphState], Awaitable[RuntimeGraphState]]
    ) -> None:
        # LangGraph's overload excludes an otherwise valid async TypedDict callable.
        graph.add_node(name, node)  # type: ignore[call-overload]

    # The explicit registrations below are the complete, reviewable workflow
    # topology.  Nodes cannot be selected dynamically by a model or policy text.
    add_node("resolve_property", partial(resolve_property, context))
    add_node("interpret", partial(interpret_message, context))
    add_node("need_information", need_information)
    add_node("retrieve_policy", partial(retrieve_policy, context))
    add_node("find_duplicates", partial(find_duplicates, context))
    add_node("select_duplicate", select_duplicate)
    add_node("prepare_create", prepare_create)
    add_node("create_ticket", partial(create_ticket, context))
    add_node("resolve_existing", partial(resolve_existing, context))
    add_node("refresh_snapshot", partial(refresh_snapshot, context))
    add_node("list_slots", partial(list_slots, context))
    add_node("select_slot", select_slot)
    add_node("prepare_book", prepare_book)
    add_node("book", partial(book, context))
    add_node("prepare_reschedule", prepare_reschedule)
    add_node("reschedule", partial(reschedule, context))
    add_node("prepare_escalate", prepare_escalate)
    add_node("escalate", partial(escalate, context))
    add_node("compose", partial(compose, context))
    add_node("finish", partial(compose, context))
    add_node("finish_safety", finish_safety)
    add_node("finish_policy_review", finish_policy_review)
    add_node("finish_unsupported", finish_unsupported)

    graph.add_edge(START, "resolve_property")
    graph.add_conditional_edges("resolve_property", route_after_property)
    graph.add_conditional_edges("interpret", route_after_interpret)
    graph.add_edge("need_information", "interpret")
    graph.add_conditional_edges("retrieve_policy", route_after_policy)
    graph.add_conditional_edges("find_duplicates", route_duplicates)
    graph.add_edge("select_duplicate", "refresh_snapshot")
    graph.add_edge("prepare_create", "create_ticket")
    graph.add_edge("create_ticket", "refresh_snapshot")
    graph.add_conditional_edges("resolve_existing", route_resolved_existing)
    graph.add_conditional_edges("refresh_snapshot", route_after_snapshot)
    graph.add_edge("list_slots", "select_slot")
    graph.add_edge("select_slot", "refresh_snapshot")
    graph.add_edge("prepare_book", "book")
    graph.add_edge("book", "refresh_snapshot")
    graph.add_edge("prepare_reschedule", "reschedule")
    graph.add_edge("reschedule", "refresh_snapshot")
    graph.add_edge("prepare_escalate", "escalate")
    graph.add_edge("escalate", "refresh_snapshot")
    for terminal in (
        "compose",
        "finish",
        "finish_safety",
        "finish_policy_review",
        "finish_unsupported",
    ):
        graph.add_edge(terminal, END)
    return graph.compile(checkpointer=checkpointer)

"""Explicit node dependencies; none are serialised into checkpoints."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.agent.nodes.compose_response import ComposeResponseNode
from app.agent.nodes.interpret_message import InterpretMessageNode
from app.agent_runtime.mcp.client import PropertyOperationsClient
from app.policy.models import PolicyRetrievalRequest, PolicyRetrievalResult


@dataclass(frozen=True, slots=True)
class NodeContext:
    """The fixed dependency boundary available to deterministic graph nodes.

    This object deliberately has no database session, repository, or transaction
    handle.  It is created by the composition root and bound to nodes only while
    the graph is being compiled; checkpoint state contains validated JSON alone.
    """

    mcp: PropertyOperationsClient
    interpret: InterpretMessageNode
    compose: ComposeResponseNode
    retrieve_policy: Callable[[PolicyRetrievalRequest], Awaitable[PolicyRetrievalResult]]


# Task-8 callers used this name before the graph split.  Keep it as a type
# alias, rather than introducing a second dependency concept.
RuntimeDependencies = NodeContext

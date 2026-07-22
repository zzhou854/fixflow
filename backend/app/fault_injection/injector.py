"""Deterministic test-only fault injection port; production uses NoOp."""

from enum import StrEnum
from typing import Protocol
from uuid import UUID


class FaultPoint(StrEnum):
    BEFORE_MCP_SEND = "BEFORE_MCP_SEND"
    AFTER_MCP_SEND = "AFTER_MCP_SEND"
    AFTER_SERVER_COMMIT_BEFORE_RESPONSE = "AFTER_SERVER_COMMIT_BEFORE_RESPONSE"
    AFTER_RESPONSE_RECEIVED_BEFORE_VALIDATION = "AFTER_RESPONSE_RECEIVED_BEFORE_VALIDATION"
    AFTER_RESULT_VALIDATED_BEFORE_CHECKPOINT = "AFTER_RESULT_VALIDATED_BEFORE_CHECKPOINT"
    # Operator escalation uses the formal Application boundary directly, not
    # an invented MCP transport. These names describe its real boundaries.
    BEFORE_MUTATION_DISPATCH = "BEFORE_MUTATION_DISPATCH"
    AFTER_MUTATION_DISPATCH = "AFTER_MUTATION_DISPATCH"
    AFTER_COMMIT_BEFORE_RESULT = "AFTER_COMMIT_BEFORE_RESULT"
    AFTER_RESULT_RECEIVED_BEFORE_VALIDATION = "AFTER_RESULT_RECEIVED_BEFORE_VALIDATION"
    AFTER_RESULT_VALIDATED_BEFORE_RUN_FINALIZATION = (
        "AFTER_RESULT_VALIDATED_BEFORE_RUN_FINALIZATION"
    )
    BEFORE_RECONCILIATION_QUERY = "BEFORE_RECONCILIATION_QUERY"
    AFTER_RECONCILIATION_QUERY_BEFORE_RESOLUTION = "AFTER_RECONCILIATION_QUERY_BEFORE_RESOLUTION"
    AFTER_RESOLUTION_COMMIT_BEFORE_ACK = "AFTER_RESOLUTION_COMMIT_BEFORE_ACK"


class FaultAction(StrEnum):
    TIMEOUT = "TIMEOUT"
    CONNECTION_RESET = "CONNECTION_RESET"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    CONTROLLED_EXCEPTION = "CONTROLLED_EXCEPTION"
    PROCESS_TERMINATION_BARRIER = "PROCESS_TERMINATION_BARRIER"


class InjectedFault(RuntimeError):
    def __init__(self, point: FaultPoint, action: FaultAction):
        super().__init__(f"injected {action.value} at {point.value}")
        self.point = point
        self.action = action


class FaultInjector(Protocol):
    async def hit(self, operation_id: UUID, point: FaultPoint) -> None: ...


class NoOpFaultInjector:
    async def hit(self, operation_id: UUID, point: FaultPoint) -> None:
        del operation_id, point


class ScriptedFaultInjector:
    """Explicit test fixture keyed by operation, point and one-based hit count."""

    def __init__(self, script: dict[tuple[UUID, FaultPoint, int], FaultAction]):
        self._script = script
        self._counts: dict[tuple[UUID, FaultPoint], int] = {}

    async def hit(self, operation_id: UUID, point: FaultPoint) -> None:
        key = (operation_id, point)
        count = self._counts.get(key, 0) + 1
        self._counts[key] = count
        action = self._script.get((operation_id, point, count))
        if action is not None:
            raise InjectedFault(point, action)

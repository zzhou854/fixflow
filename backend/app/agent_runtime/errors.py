"""Transport-neutral and sanitized runtime failures."""


class AgentRuntimeError(Exception):
    code = "AGENT_RUNTIME_ERROR"


class PropertyContextRequired(AgentRuntimeError):
    code = "PROPERTY_CONTEXT_REQUIRED"


class ThreadIdentityConflict(AgentRuntimeError):
    code = "THREAD_IDENTITY_CONFLICT"


class PropertyContextConflict(AgentRuntimeError):
    code = "PROPERTY_CONTEXT_CONFLICT"


class ResumeConflict(AgentRuntimeError):
    code = "RESUME_CONFLICT"


class MCPClientError(AgentRuntimeError):
    code = "MCP_CLIENT_ERROR"


class MCPUnavailable(MCPClientError):
    code = "MCP_UNAVAILABLE"


class MCPTimeout(MCPClientError):
    code = "MCP_TIMEOUT"


class MCPContractViolation(MCPClientError):
    code = "MCP_CONTRACT_VIOLATION"


class MCPToolNotFound(MCPClientError):
    code = "MCP_TOOL_NOT_FOUND"


class MCPToolResultError(MCPClientError):
    def __init__(self, result_code: str) -> None:
        super().__init__(result_code)
        self.code = result_code


class MutationNotSent(MCPClientError):
    code = "NOT_SENT"


class UnknownCommit(MCPClientError):
    code = "UNKNOWN_COMMIT"

    def __init__(self, operation_id: object, action: str) -> None:
        super().__init__("mutation result is unknown")
        self.operation_id = operation_id
        self.action = action


class PolicyRetrievalFailed(AgentRuntimeError):
    """Policy retrieval/merge failed; the graph must never assume sufficiency."""

    code = "POLICY_RETRIEVAL_FAILED"

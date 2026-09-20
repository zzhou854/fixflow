"""Structured Agent/LLM errors independent of HTTP and persistence."""


class AgentError(Exception):
    """Base error suitable for later Trace recording."""

    code = "agent_error"


class LLMProviderUnavailable(AgentError):
    code = "llm_provider_unavailable"


class LLMTimeout(AgentError):
    code = "llm_timeout"


class StructuredOutputInvalid(AgentError):
    code = "structured_output_invalid"


class ProviderExhausted(AgentError):
    code = "provider_exhausted"


class PromptInputInvalid(AgentError):
    code = "prompt_input_invalid"


class StateMergeConflict(AgentError):
    code = "state_merge_conflict"

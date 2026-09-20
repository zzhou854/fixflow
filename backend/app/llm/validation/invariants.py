"""Cross-field invariants that remain deterministic outside the model."""

from app.agent.enums import AcceptanceDecision, AgentIntent
from app.agent.models import InterpretMessageOutput
from app.llm.errors import LLMProviderError, LLMProviderErrorCode


class InterpretationInvariantValidator:
    def validate(
        self,
        value: InterpretMessageOutput,
        *,
        provider: str,
        model: str,
    ) -> InterpretMessageOutput:
        if value.requested_human != (value.utterance_intent is AgentIntent.REQUEST_HUMAN):
            raise LLMProviderError(
                LLMProviderErrorCode.INVARIANT_VIOLATION,
                provider=provider,
                model=model,
                retryable=False,
                safe_detail="requested_human conflicts with utterance_intent",
            )
        if (
            value.utterance_intent is AgentIntent.ACCEPT_REPAIR
            and value.acceptance_decision is not AcceptanceDecision.ACCEPT
        ):
            self._reject(provider, model, "acceptance decision conflicts with intent")
        if (
            value.utterance_intent is AgentIntent.REJECT_REPAIR
            and value.acceptance_decision is not AcceptanceDecision.REJECT
        ):
            self._reject(provider, model, "acceptance decision conflicts with intent")
        if value.utterance_intent not in {
            AgentIntent.ACCEPT_REPAIR,
            AgentIntent.REJECT_REPAIR,
        } and value.acceptance_decision not in {None, AcceptanceDecision.UNCLEAR}:
            self._reject(provider, model, "acceptance decision is not allowed for this intent")
        return value

    @staticmethod
    def _reject(provider: str, model: str, detail: str) -> None:
        raise LLMProviderError(
            LLMProviderErrorCode.INVARIANT_VIOLATION,
            provider=provider,
            model=model,
            retryable=False,
            safe_detail=detail,
        )

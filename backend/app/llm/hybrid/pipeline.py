"""Fact extraction followed by deterministic final interpretation."""

from __future__ import annotations

import hashlib
import inspect
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import ValidationError

from app.agent.enums import LLMRole
from app.agent.errors import ProviderExhausted, StructuredOutputInvalid
from app.agent.models import (
    InterpretationNodeResult,
    InterpretMessageInput,
    InterpretMessageOutput,
    LLMMessage,
    LLMRequestConfig,
    NodeMetadata,
    TimeWindow,
)
from app.agent.ports import LLMProvider
from app.llm.errors import LLMProviderError
from app.llm.hybrid.decision import (
    InterpretationConflictDetector,
    ResidentIntentDecisionEngine,
    resolved_issue_category,
)
from app.llm.hybrid.models import (
    ARCHITECTURE_ID,
    ARCHITECTURE_VERSION,
    DECISION_ENGINE_VERSION,
    REQUIREMENTS_POLICY_VERSION,
    SAFETY_POLICY_VERSION,
    ControlledVerificationResult,
    ExtractedResidentFactsV2,
    HybridDecision,
    HybridInterpretationMetadata,
    NormalizedResidentFactsV1,
)
from app.llm.hybrid.normalizer import ResidentFactNormalizer
from app.llm.hybrid.prompts import FactPromptRegistry
from app.llm.hybrid.semantic import ResidentSemanticActsV1, derive_semantic_acts
from app.llm.online.routing import ProviderExhaustedError
from app.llm.sanitizer import InterpretationInputLimits, build_sanitized_interpretation_input

Verifier = Callable[
    [InterpretMessageInput, NormalizedResidentFactsV1, tuple[str, ...]],
    Awaitable[ControlledVerificationResult],
]


@dataclass(frozen=True, slots=True)
class HybridInterpretationDiagnostics:
    facts: NormalizedResidentFactsV1
    semantic_acts: ResidentSemanticActsV1
    decision: HybridDecision
    metadata: HybridInterpretationMetadata


class HybridInterpretationNode:
    """Graph-compatible node; LLM extracts facts and code owns final decisions."""

    def __init__(
        self,
        fact_provider: LLMProvider,
        *,
        model: str,
        prompt_registry: FactPromptRegistry | None = None,
        prompt_version: str | None = None,
        input_limits: InterpretationInputLimits | None = None,
        verifier: Verifier | None = None,
    ) -> None:
        self._provider = fact_provider
        self._model = model
        self._prompts = prompt_registry or FactPromptRegistry()
        self._prompt_version = prompt_version
        self._limits = input_limits or InterpretationInputLimits()
        self._normalizer = ResidentFactNormalizer()
        self._decision = ResidentIntentDecisionEngine()
        self._conflicts = InterpretationConflictDetector()
        self._verifier = verifier
        self.last_diagnostics: HybridInterpretationDiagnostics | None = None

    async def __call__(self, node_input: InterpretMessageInput) -> InterpretationNodeResult:
        result, diagnostics = await self.interpret_with_diagnostics(node_input)
        self.last_diagnostics = diagnostics
        return result

    async def interpret_with_diagnostics(
        self,
        node_input: InterpretMessageInput,
    ) -> tuple[InterpretationNodeResult, HybridInterpretationDiagnostics]:
        prompt = self._prompts.resident_fact_extraction(self._prompt_version)
        sanitized = build_sanitized_interpretation_input(node_input, self._limits)
        schema_json = json.dumps(prompt.output_schema, ensure_ascii=False, sort_keys=True)
        messages = (
            LLMMessage(
                role=LLMRole.SYSTEM,
                content=f"{prompt.system_template}\n\nExpected JSON Schema:\n{schema_json}",
            ),
            LLMMessage(
                role=LLMRole.USER,
                content=f"Extract facts from this bounded JSON context:\n{sanitized.payload_json}",
            ),
        )
        try:
            raw = await self._provider.generate_structured(
                messages=messages,
                response_model=ExtractedResidentFactsV2,
                model_config=LLMRequestConfig(
                    model=self._model,
                    prompt_name=prompt.prompt_id,
                    prompt_version=prompt.prompt_version,
                    temperature=0,
                    max_output_tokens=1600,
                ),
            )
        except ProviderExhaustedError as exc:
            raise ProviderExhausted("online structured providers exhausted") from exc
        except LLMProviderError as exc:
            raise ProviderExhausted("online structured provider unavailable") from exc
        try:
            extracted = ExtractedResidentFactsV2.model_validate(raw.payload)
        except ValidationError as exc:
            raise StructuredOutputInvalid("resident fact extraction failed validation") from exc
        facts = self._normalizer.normalize(
            extracted,
            current_user_message=node_input.current_user_message,
        )
        semantic_acts = derive_semantic_acts(facts, node_input=node_input)
        decision = self._decision.decide(facts, node_input=node_input)
        conflicts = self._conflicts.detect(facts, decision, node_input=node_input)
        verification_count = 0
        verification_reason: str | None = None
        verification_result = None
        if conflicts and self._verifier is not None:
            verification_count = 1
            verification_reason = ",".join(item.value for item in conflicts)
            verification_result = await self._verifier(
                node_input,
                facts,
                tuple(item.value for item in conflicts),
            )
        final = InterpretMessageOutput(
            utterance_intent=decision.utterance_intent,
            issue_category=resolved_issue_category(facts),
            issue_location=facts.location_text,
            issue_description_update=(
                facts.issue_description_text if facts.issue_description_present else None
            ),
            safety_flags=decision.safety_flags,
            user_availability_windows=tuple(
                TimeWindow.model_validate(window.model_dump())
                for window in facts.availability_windows
            ),
            user_correction=facts.correction_present,
            acceptance_decision=facts.acceptance_decision,
            requested_human=decision.requested_human,
            model_suggested_missing_fields=decision.missing_fields,
        )
        architecture_hash = _architecture_hash(prompt.prompt_hash)
        hybrid_metadata = HybridInterpretationMetadata(
            architecture_id=ARCHITECTURE_ID,
            architecture_version=ARCHITECTURE_VERSION,
            architecture_hash=architecture_hash,
            fact_prompt_id=prompt.prompt_id,
            fact_prompt_version=prompt.prompt_version,
            fact_prompt_hash=prompt.prompt_hash,
            decision_engine_version=DECISION_ENGINE_VERSION,
            safety_policy_version=SAFETY_POLICY_VERSION,
            requirements_policy_version=REQUIREMENTS_POLICY_VERSION,
            verification_call_count=verification_count,
            verification_reason=verification_reason,
            verification_result=(
                verification_result.verdict if verification_result is not None else None
            ),
            final_decision_source=(
                "DETERMINISTIC_AFTER_VERIFICATION" if verification_count else "DETERMINISTIC"
            ),
        )
        node_result = InterpretationNodeResult(
            interpretation=final,
            metadata=NodeMetadata(
                provider=raw.provider,
                model=raw.model,
                prompt_name=prompt.prompt_id,
                prompt_version=prompt.prompt_version,
                prompt_hash=raw.prompt_hash or prompt.prompt_hash,
                schema_version="interpretation-result-v1",
                request_id=raw.request_id,
                finish_reason=raw.finish_reason,
                latency_ms=raw.latency_ms,
                attempt_count=raw.attempt_count,
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                total_tokens=raw.total_tokens,
                thinking_mode=raw.thinking_mode,
                transport_tool_call_count=raw.transport_tool_call_count,
                json_decoded=raw.json_decoded,
                schema_validated=raw.schema_validated,
                invariants_validated=raw.invariants_validated,
            ),
        )
        diagnostics = HybridInterpretationDiagnostics(
            facts=facts,
            semantic_acts=semantic_acts,
            decision=decision,
            metadata=hybrid_metadata,
        )
        self.last_diagnostics = diagnostics
        return node_result, diagnostics


def _architecture_hash(prompt_hash: str) -> str:
    components = {
        "architecture_id": ARCHITECTURE_ID,
        "architecture_version": ARCHITECTURE_VERSION,
        "decision_engine_version": DECISION_ENGINE_VERSION,
        "fact_prompt_hash": prompt_hash,
        "requirements_policy_version": REQUIREMENTS_POLICY_VERSION,
        "safety_policy_version": SAFETY_POLICY_VERSION,
        "pipeline_source": inspect.getsource(HybridInterpretationNode),
    }
    canonical = json.dumps(components, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()

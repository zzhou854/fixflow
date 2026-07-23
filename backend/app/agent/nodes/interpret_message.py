"""Bounded structured interpretation node with no business side effects."""

import json
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.agent.enums import LLMRole
from app.agent.errors import AgentError, LLMProviderUnavailable, LLMTimeout, StructuredOutputInvalid
from app.agent.models import (
    InterpretationNodeResult,
    InterpretMessageInput,
    InterpretMessageOutput,
    LLMMessage,
    LLMRequestConfig,
    NodeMetadata,
)
from app.agent.ports import LLMProvider
from app.llm.errors import LLMProviderError, LLMProviderErrorCode
from app.llm.prompts.registry import PromptRegistry
from app.llm.sanitizer import (
    InterpretationInputLimits,
    build_sanitized_interpretation_input,
)
from app.llm.validation.invariants import InterpretationInvariantValidator


class InterpretMessageNode:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str,
        input_limits: InterpretationInputLimits | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self._provider = provider
        self._model = model
        self._input_limits = input_limits or InterpretationInputLimits()
        self._prompt_registry = prompt_registry or PromptRegistry()
        self._invariants = InterpretationInvariantValidator()

    async def __call__(self, node_input: InterpretMessageInput) -> InterpretationNodeResult:
        prompt = self._prompt_registry.resident_interpretation()
        sanitized = build_sanitized_interpretation_input(node_input, self._input_limits)
        config = LLMRequestConfig(
            model=self._model,
            prompt_name=prompt.prompt_id,
            prompt_version=prompt.prompt_version,
            temperature=0,
            max_output_tokens=1400,
        )
        schema_json = json.dumps(prompt.output_schema, ensure_ascii=False, sort_keys=True)
        examples_json = json.dumps(
            [example.model_dump(mode="json") for example in prompt.examples],
            ensure_ascii=False,
            sort_keys=True,
        )
        messages = (
            LLMMessage(
                role=LLMRole.SYSTEM,
                content=(
                    f"{prompt.system_template}\n\nExpected JSON Schema:\n"
                    f"{schema_json}"
                    "\n\nValidated examples:\n"
                    f"{examples_json}"
                ),
            ),
            LLMMessage(
                role=LLMRole.USER,
                content=f"Interpret this bounded JSON context:\n{sanitized.payload_json}",
            ),
        )
        try:
            raw = await self._provider.generate_structured(
                messages=messages,
                response_model=InterpretMessageOutput,
                model_config=config,
            )
        except LLMProviderError as exc:
            if exc.code is LLMProviderErrorCode.TIMEOUT:
                raise LLMTimeout("interpret_message provider timed out") from exc
            if exc.code in {
                LLMProviderErrorCode.INVALID_RESPONSE,
                LLMProviderErrorCode.EMPTY_RESPONSE,
                LLMProviderErrorCode.INVALID_JSON,
                LLMProviderErrorCode.SCHEMA_VALIDATION_FAILED,
                LLMProviderErrorCode.INVARIANT_VIOLATION,
            }:
                raise StructuredOutputInvalid("interpret_message output failed validation") from exc
            raise LLMProviderUnavailable("interpret_message provider unavailable") from exc
        except AgentError:
            raise
        except TimeoutError as exc:
            raise LLMTimeout("interpret_message provider timed out") from exc
        except (ConnectionError, OSError) as exc:
            raise LLMProviderUnavailable("interpret_message provider unavailable") from exc
        try:
            interpretation = InterpretMessageOutput.model_validate(raw.payload)
            interpretation = self._invariants.validate(
                interpretation,
                provider=raw.provider,
                model=raw.model,
            )
        except (ValidationError, LLMProviderError) as exc:
            raise StructuredOutputInvalid("interpret_message output failed validation") from exc
        timezone = ZoneInfo(node_input.timezone_name)
        for window in interpretation.user_availability_windows:
            if (
                window.starts_at.utcoffset() != window.starts_at.astimezone(timezone).utcoffset()
                or window.ends_at.utcoffset() != window.ends_at.astimezone(timezone).utcoffset()
            ):
                raise StructuredOutputInvalid(
                    "interpret_message availability does not match the requested timezone"
                )
        return InterpretationNodeResult(
            interpretation=interpretation,
            metadata=NodeMetadata(
                provider=raw.provider,
                model=raw.model,
                prompt_name=prompt.prompt_id,
                prompt_version=prompt.prompt_version,
                prompt_hash=raw.prompt_hash or prompt.prompt_hash,
                schema_version=raw.schema_version or prompt.schema_version,
                request_id=raw.request_id,
                finish_reason=raw.finish_reason,
                latency_ms=raw.latency_ms,
                attempt_count=raw.attempt_count,
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                total_tokens=raw.total_tokens,
                thinking_mode=raw.thinking_mode,
                json_decoded=raw.json_decoded,
                schema_validated=raw.schema_validated,
                invariants_validated=raw.invariants_validated,
            ),
        )

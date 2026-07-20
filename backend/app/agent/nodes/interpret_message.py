"""Bounded structured interpretation node with no business side effects."""

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
from app.agent.prompts.interpret_message import INTERPRET_MESSAGE_PROMPT


class InterpretMessageNode:
    def __init__(self, provider: LLMProvider, *, model: str) -> None:
        self._provider = provider
        self._model = model

    async def __call__(self, node_input: InterpretMessageInput) -> InterpretationNodeResult:
        prompt = INTERPRET_MESSAGE_PROMPT
        config = LLMRequestConfig(
            model=self._model,
            prompt_name=prompt.prompt_name,
            prompt_version=prompt.prompt_version,
            temperature=0,
            max_output_tokens=1400,
        )
        messages = (
            LLMMessage(role=LLMRole.SYSTEM, content=prompt.system_instruction),
            LLMMessage(
                role=LLMRole.USER,
                content=prompt.input_template.format(
                    input_json=node_input.model_dump_json(exclude_none=True)
                ),
            ),
        )
        try:
            raw = await self._provider.generate_structured(
                messages=messages,
                response_model=InterpretMessageOutput,
                model_config=config,
            )
        except AgentError:
            raise
        except TimeoutError as exc:
            raise LLMTimeout("interpret_message provider timed out") from exc
        except (ConnectionError, OSError) as exc:
            raise LLMProviderUnavailable("interpret_message provider unavailable") from exc
        try:
            interpretation = InterpretMessageOutput.model_validate(raw.payload)
        except ValidationError as exc:
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
                prompt_name=prompt.prompt_name,
                prompt_version=prompt.prompt_version,
            ),
        )

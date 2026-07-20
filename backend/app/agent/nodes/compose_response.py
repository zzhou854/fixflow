"""Evidence-bounded response-composition node with no business side effects."""

from app.agent.enums import LLMRole
from app.agent.errors import AgentError, LLMProviderUnavailable, LLMTimeout
from app.agent.models import (
    ComposeResponseInput,
    ComposeResponseResult,
    LLMMessage,
    LLMRequestConfig,
    NodeMetadata,
)
from app.agent.ports import LLMProvider
from app.agent.prompts.compose_response import COMPOSE_RESPONSE_PROMPT


class ComposeResponseNode:
    def __init__(self, provider: LLMProvider, *, model: str) -> None:
        self._provider = provider
        self._model = model

    async def __call__(self, node_input: ComposeResponseInput) -> ComposeResponseResult:
        prompt = COMPOSE_RESPONSE_PROMPT
        config = LLMRequestConfig(
            model=self._model,
            prompt_name=prompt.prompt_name,
            prompt_version=prompt.prompt_version,
            temperature=0,
            max_output_tokens=800,
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
            raw = await self._provider.generate_response(messages=messages, model_config=config)
        except AgentError:
            raise
        except TimeoutError as exc:
            raise LLMTimeout("compose_response provider timed out") from exc
        except (ConnectionError, OSError) as exc:
            raise LLMProviderUnavailable("compose_response provider unavailable") from exc
        return ComposeResponseResult(
            response_text=raw.text,
            metadata=NodeMetadata(
                provider=raw.provider,
                model=raw.model,
                prompt_name=prompt.prompt_name,
                prompt_version=prompt.prompt_version,
            ),
        )

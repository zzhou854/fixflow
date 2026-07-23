"""Centralized provider selection at application startup."""

from app.agent.ports import LLMProvider
from app.config import Settings
from app.llm.prompts.registry import PromptRegistry
from app.llm.providers.zai_glm import (
    ZaiGLMConfig,
    ZaiGLMStructuredInterpretationProvider,
)
from app.llm.validation.parser import StructuredInterpretationParser


def build_structured_interpretation_provider(
    settings: Settings,
    *,
    scripted_provider: LLMProvider,
) -> LLMProvider:
    if settings.llm_provider == "scripted":
        return scripted_provider
    if settings.llm_provider != "glm":
        raise ValueError(f"unsupported LLM provider: {settings.llm_provider}")
    assert settings.glm_api_key is not None
    prompt = PromptRegistry().resident_interpretation()
    return ZaiGLMStructuredInterpretationProvider(
        prompt=prompt,
        parser=StructuredInterpretationParser(max_response_bytes=settings.llm_max_response_bytes),
        config=ZaiGLMConfig(
            model=settings.glm_model,
            base_url=settings.glm_base_url,
            thinking_mode=settings.glm_thinking_mode,
            temperature=settings.glm_temperature,
            top_p=settings.glm_top_p,
            max_tokens=settings.glm_max_tokens,
            request_timeout_seconds=settings.glm_request_timeout_seconds,
            total_timeout_seconds=settings.glm_total_timeout_seconds,
            max_attempts=settings.glm_max_attempts,
            retry_initial_delay_seconds=settings.glm_retry_initial_delay_seconds,
            retry_max_delay_seconds=settings.glm_retry_max_delay_seconds,
            max_concurrency=settings.glm_max_concurrency,
        ),
        api_key=settings.glm_api_key.get_secret_value(),
    )

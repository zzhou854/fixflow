"""Centralized provider selection at application startup."""

from app.agent.ports import LLMProvider
from app.config import Settings
from app.llm.prompts.registry import PromptRegistry
from app.llm.providers.deepseek import (
    DeepSeekConfig,
    DeepSeekStructuredInterpretationProvider,
)
from app.llm.providers.zai_glm import (
    ZaiGLMConfig,
    ZaiGLMStructuredInterpretationProvider,
)
from app.llm.validation.parser import StructuredInterpretationParser


def build_structured_interpretation_provider(
    settings: Settings,
    *,
    scripted_provider: LLMProvider,
    prompt_version: str | None = None,
    provider_max_attempts: int | None = None,
    provider_max_concurrency: int | None = None,
) -> LLMProvider:
    if settings.llm_provider == "scripted":
        return scripted_provider
    prompt = PromptRegistry().resident_interpretation(prompt_version)
    parser = StructuredInterpretationParser(max_response_bytes=settings.llm_max_response_bytes)
    if settings.llm_provider == "deepseek":
        assert settings.deepseek_api_key is not None
        return DeepSeekStructuredInterpretationProvider(
            prompt=prompt,
            parser=parser,
            config=DeepSeekConfig(
                model=settings.deepseek_model,
                base_url=settings.deepseek_base_url,
                thinking_mode=settings.deepseek_thinking_mode,
                temperature=settings.deepseek_temperature,
                top_p=settings.deepseek_top_p,
                max_tokens=settings.deepseek_max_tokens,
                request_timeout_seconds=settings.deepseek_request_timeout_seconds,
                total_timeout_seconds=settings.deepseek_total_timeout_seconds,
                max_attempts=provider_max_attempts or settings.deepseek_max_attempts,
                retry_initial_delay_seconds=settings.deepseek_retry_initial_delay_seconds,
                retry_max_delay_seconds=settings.deepseek_retry_max_delay_seconds,
                max_concurrency=provider_max_concurrency or settings.deepseek_max_concurrency,
            ),
            api_key=settings.deepseek_api_key.get_secret_value(),
        )
    if settings.llm_provider != "glm":
        raise ValueError(f"unsupported LLM provider: {settings.llm_provider}")
    assert settings.glm_api_key is not None
    return ZaiGLMStructuredInterpretationProvider(
        prompt=prompt,
        parser=parser,
        config=ZaiGLMConfig(
            model=settings.glm_model,
            base_url=settings.glm_base_url,
            thinking_mode=settings.glm_thinking_mode,
            temperature=settings.glm_temperature,
            top_p=settings.glm_top_p,
            max_tokens=settings.glm_max_tokens,
            request_timeout_seconds=settings.glm_request_timeout_seconds,
            total_timeout_seconds=settings.glm_total_timeout_seconds,
            max_attempts=provider_max_attempts or settings.glm_max_attempts,
            retry_initial_delay_seconds=settings.glm_retry_initial_delay_seconds,
            retry_max_delay_seconds=settings.glm_retry_max_delay_seconds,
            max_concurrency=provider_max_concurrency or settings.glm_max_concurrency,
        ),
        api_key=settings.glm_api_key.get_secret_value(),
    )

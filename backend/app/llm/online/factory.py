"""Construction of inactive-by-default DeepSeek online candidates."""

import hashlib
from dataclasses import dataclass

from app.config import Settings
from app.llm.hybrid.prompts import FactPromptRegistry
from app.llm.online.gate import (
    OnlineProviderGate,
    OnlineRuntimeMode,
    ProviderPurpose,
    QualificationStatus,
)
from app.llm.online.grounded import (
    GROUNDED_PROMPT_TEMPLATE,
    GROUNDED_PROMPT_VERSION,
    GROUNDED_SCHEMA_VERSION,
)
from app.llm.online.routing import DeepSeekStructuredRouter, RoutingPolicy
from app.llm.providers.deepseek import (
    DeepSeekConfig,
    DeepSeekStructuredInterpretationProvider,
    StructuredPromptIdentity,
)
from app.llm.validation.parser import StructuredInterpretationParser


def online_gate_from_settings(settings: Settings) -> OnlineProviderGate:
    return OnlineProviderGate(
        enabled=settings.llm_online_enabled,
        shadow_enabled=settings.llm_shadow_enabled,
        mode=OnlineRuntimeMode(settings.llm_online_runtime_mode),
        qualification=QualificationStatus(settings.llm_online_qualification_status),
    )


def build_deepseek_online_candidate(
    settings: Settings,
    *,
    purpose: ProviderPurpose,
) -> DeepSeekStructuredRouter:
    online_gate_from_settings(settings).require(purpose)
    prompt = FactPromptRegistry().resident_fact_extraction()
    return _build_router(settings, prompt=prompt)


@dataclass(frozen=True, slots=True)
class GroundedPromptIdentity:
    prompt_id: str = "grounded_response"
    prompt_version: str = GROUNDED_PROMPT_VERSION
    prompt_hash: str = hashlib.sha256(GROUNDED_PROMPT_TEMPLATE.encode()).hexdigest()
    schema_version: str = GROUNDED_SCHEMA_VERSION


def build_deepseek_grounded_candidate(
    settings: Settings,
    *,
    purpose: ProviderPurpose,
) -> DeepSeekStructuredRouter:
    """Build the presentation-only candidate under the same development gate."""

    online_gate_from_settings(settings).require(purpose)
    return _build_router(settings, prompt=GroundedPromptIdentity())


def _build_router(
    settings: Settings, *, prompt: StructuredPromptIdentity
) -> DeepSeekStructuredRouter:
    if settings.deepseek_api_key is None:
        raise ValueError("DEEPSEEK_API_KEY is required for an authorized online call")
    parser = StructuredInterpretationParser(max_response_bytes=settings.llm_max_response_bytes)

    def config(model: str) -> DeepSeekConfig:
        return DeepSeekConfig(
            model=model,
            base_url=settings.deepseek_base_url,
            thinking_mode="disabled",
            temperature=settings.deepseek_temperature,
            top_p=settings.deepseek_top_p,
            max_tokens=settings.deepseek_max_tokens,
            request_timeout_seconds=min(
                settings.deepseek_request_timeout_seconds,
                settings.llm_model_budget_seconds,
            ),
            total_timeout_seconds=min(
                settings.deepseek_total_timeout_seconds,
                settings.llm_model_budget_seconds,
            ),
            max_attempts=1,
            max_concurrency=settings.deepseek_max_concurrency,
        )

    api_key = settings.deepseek_api_key.get_secret_value()
    flash = DeepSeekStructuredInterpretationProvider(
        prompt=prompt,
        parser=parser,
        config=config("deepseek-v4-flash"),
        api_key=api_key,
    )
    pro = DeepSeekStructuredInterpretationProvider(
        prompt=prompt,
        parser=parser,
        config=config("deepseek-v4-pro"),
        api_key=api_key,
    )
    return DeepSeekStructuredRouter(
        flash=flash,
        pro=pro,
        policy=RoutingPolicy(
            flash_transport_attempts=2,
            pro_transport_attempts=1,
            per_call_timeout_seconds=min(
                settings.deepseek_request_timeout_seconds,
                settings.llm_model_budget_seconds,
            ),
        ),
    )

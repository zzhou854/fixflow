"""Explicit opt-in guard executed before any paid-client construction."""

from __future__ import annotations

from app.llm.evaluation.errors import OnlineGuardError


def require_online_authorization(
    *,
    provider: str,
    allow_network: bool,
    acknowledge_cost: bool,
    api_key_available: bool,
) -> None:
    if provider not in {"glm", "deepseek"}:
        return
    label = "GLM" if provider == "glm" else "DeepSeek"
    key_name = "GLM_API_KEY" if provider == "glm" else "DEEPSEEK_API_KEY"
    if not allow_network:
        raise OnlineGuardError(f"{label} evaluation requires --allow-network")
    if not acknowledge_cost:
        raise OnlineGuardError(f"{label} evaluation requires --acknowledge-cost")
    if not api_key_available:
        raise OnlineGuardError(f"{label} evaluation requires {key_name} in existing settings")

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
    if provider != "glm":
        return
    if not allow_network:
        raise OnlineGuardError("GLM evaluation requires --allow-network")
    if not acknowledge_cost:
        raise OnlineGuardError("GLM evaluation requires --acknowledge-cost")
    if not api_key_available:
        raise OnlineGuardError("GLM evaluation requires GLM_API_KEY in existing settings")

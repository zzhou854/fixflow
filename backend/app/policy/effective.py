"""Pure policy effective-time rule mirrored by SQL retrieval filters."""

from datetime import datetime
from typing import Protocol

from app.policy.models import require_aware


class EffectivePolicy(Protocol):
    @property
    def effective_from(self) -> datetime: ...
    @property
    def effective_to(self) -> datetime | None: ...
    @property
    def is_enabled(self) -> bool: ...


def is_policy_effective(policy: EffectivePolicy, as_of: datetime) -> bool:
    require_aware(as_of, "as_of")
    return (
        policy.is_enabled
        and policy.effective_from <= as_of
        and (policy.effective_to is None or as_of < policy.effective_to)
    )

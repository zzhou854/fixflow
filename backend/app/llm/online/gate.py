"""Explicit qualification/activation gate for online calls."""

from dataclasses import dataclass
from enum import StrEnum


class OnlineRuntimeMode(StrEnum):
    DEVELOPMENT = "development"
    DEMO_SAFE = "demo_safe"
    PRODUCTION_CANDIDATE = "production_candidate"


class QualificationStatus(StrEnum):
    NOT_ACTIVATED = "NOT_ACTIVATED"
    CONTRACT_PASSED = "CONTRACT_PASSED"
    DEV_REGRESSION_PASSED = "DEV_REGRESSION_PASSED"
    HOLDOUT_PASSED = "HOLDOUT_PASSED"
    SHADOW_PASSED = "SHADOW_PASSED"
    CANARY_PASSED = "CANARY_PASSED"
    APPROVED = "APPROVED"


class ProviderPurpose(StrEnum):
    TEST = "TEST"
    SHADOW = "SHADOW"
    BUSINESS = "BUSINESS"


_ORDER = {
    status: index
    for index, status in enumerate(
        (
            QualificationStatus.NOT_ACTIVATED,
            QualificationStatus.CONTRACT_PASSED,
            QualificationStatus.DEV_REGRESSION_PASSED,
            QualificationStatus.HOLDOUT_PASSED,
            QualificationStatus.SHADOW_PASSED,
            QualificationStatus.CANARY_PASSED,
            QualificationStatus.APPROVED,
        )
    )
}


@dataclass(frozen=True, slots=True)
class OnlineProviderGate:
    enabled: bool = False
    shadow_enabled: bool = False
    mode: OnlineRuntimeMode = OnlineRuntimeMode.DEMO_SAFE
    qualification: QualificationStatus = QualificationStatus.NOT_ACTIVATED

    def allows(self, purpose: ProviderPurpose) -> bool:
        if not self.enabled:
            return False
        if purpose is ProviderPurpose.TEST:
            return self.mode is OnlineRuntimeMode.DEVELOPMENT
        if purpose is ProviderPurpose.SHADOW:
            return (
                self.shadow_enabled
                and _ORDER[self.qualification] >= _ORDER[QualificationStatus.CONTRACT_PASSED]
            )
        return (
            self.mode is OnlineRuntimeMode.PRODUCTION_CANDIDATE
            and self.qualification is QualificationStatus.APPROVED
        )

    def require(self, purpose: ProviderPurpose) -> None:
        if not self.allows(purpose):
            raise RuntimeError(f"online provider is not authorized for {purpose.value}")

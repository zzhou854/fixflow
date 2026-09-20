"""Typed evaluation failures and stable CLI exit codes."""

from enum import IntEnum


class EvaluationExitCode(IntEnum):
    SUCCESS = 0
    GATE_FAILED = 1
    INVALID_INPUT = 2
    INFRASTRUCTURE_FAILED = 3
    INCOMPLETE = 4
    INCOMPATIBLE = 5
    ONLINE_GUARD_FAILED = 6


class EvaluationError(Exception):
    """Base class for safe evaluation failures."""


class DatasetValidationError(EvaluationError):
    pass


class PolicyValidationError(EvaluationError):
    pass


class ArtifactError(EvaluationError):
    pass


class ResumeIdentityError(EvaluationError):
    pass


class OnlineGuardError(EvaluationError):
    pass


class ComparisonIncompatibleError(EvaluationError):
    pass

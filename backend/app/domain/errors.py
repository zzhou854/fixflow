"""Structured exceptions raised by pure domain rules."""

from collections.abc import Mapping
from types import MappingProxyType


class DomainError(Exception):
    """Base error carrying a stable code and structured diagnostic context."""

    def __init__(self, code: str, **context: object) -> None:
        self.code = code
        self.context: Mapping[str, object] = MappingProxyType(dict(context))
        super().__init__(code)


class InvalidTicketTransition(DomainError):
    """Requested ticket transition is not present in the frozen matrix."""


class InvalidAppointmentTransition(DomainError):
    """Requested appointment transition violates its frozen lifecycle."""


class InvalidWorkerEvent(DomainError):
    """Worker event is unauthorized, incomplete, or out of sequence."""


class InvalidEscalationRecovery(DomainError):
    """Escalated ticket cannot safely resume from the supplied snapshot."""


class VersionConflict(DomainError):
    """Expected aggregate version differs from the current version."""


class InvariantViolation(DomainError):
    """A frozen cross-entity business invariant was violated."""


class PermissionDenied(DomainError):
    """Actor is not allowed to perform the requested domain action."""

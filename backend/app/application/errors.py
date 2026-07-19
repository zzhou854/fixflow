"""Stable application and persistence-boundary failures."""

from collections.abc import Mapping
from types import MappingProxyType


class ApplicationError(Exception):
    """A use-case failure safe to map to a typed external result."""

    def __init__(self, code: str, **context: object) -> None:
        self.code = code
        self.context: Mapping[str, object] = MappingProxyType(dict(context))
        super().__init__(code)


class ResourceNotFound(ApplicationError):
    """A requested aggregate does not exist."""


class AuthorizationFailed(ApplicationError):
    """The authenticated actor cannot perform the use case."""


class IdempotencyConflict(ApplicationError):
    """An idempotency identity was reused for a different request."""


class PersistenceConflict(ApplicationError):
    """A named database invariant rejected a concurrent write."""


class ActiveAppointmentExists(PersistenceConflict):
    """A ticket already has a current BOOKED appointment."""


class AppointmentTimeConflict(PersistenceConflict):
    """A worker already has an overlapping BOOKED appointment."""


class DatabaseIntegrityFailure(PersistenceConflict):
    """An unmapped database integrity class failed the transaction."""

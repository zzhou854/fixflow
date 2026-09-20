"""Four deterministic committed-outcome validators."""

from typing import Protocol


class VersionedEntity(Protocol):
    version: int


def _version_matches(entity: VersionedEntity, payload: dict[str, object]) -> bool:
    value = payload.get("resource_version")
    return isinstance(value, int) and entity.version == value


def validate_create_ticket(
    entity: VersionedEntity, payload: dict[str, object], event_types: set[str]
) -> bool:
    return _version_matches(entity, payload) and "ticket.created" in event_types


def validate_book_appointment(
    entity: VersionedEntity, payload: dict[str, object], event_types: set[str]
) -> bool:
    return _version_matches(entity, payload) and "appointment.booked" in event_types


def validate_reschedule_appointment(
    entity: VersionedEntity, payload: dict[str, object], event_types: set[str]
) -> bool:
    return _version_matches(entity, payload) and "appointment.rescheduled" in event_types


def validate_escalate_ticket(
    entity: VersionedEntity, payload: dict[str, object], event_types: set[str]
) -> bool:
    return _version_matches(entity, payload) and "ticket.escalated" in event_types

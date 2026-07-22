"""Closed delivery classification for mutation calls."""

from enum import StrEnum

from app.property_operations.contracts.common import ResultCode


class MutationDeliveryClassification(StrEnum):
    KNOWN_SUCCESS = "KNOWN_SUCCESS"
    KNOWN_FAILURE = "KNOWN_FAILURE"
    NOT_SENT = "NOT_SENT"
    UNKNOWN_COMMIT = "UNKNOWN_COMMIT"


_KNOWN_SUCCESS_CODES = frozenset({ResultCode.CREATED, ResultCode.UPDATED})
_KNOWN_FAILURE_CODES = frozenset(
    {
        ResultCode.PERMISSION_DENIED,
        ResultCode.NOT_FOUND,
        ResultCode.VERSION_CONFLICT,
        ResultCode.TIME_CONFLICT,
        ResultCode.IDEMPOTENCY_CONFLICT,
        ResultCode.VALIDATION_ERROR,
        ResultCode.UNSUPPORTED_OPERATION,
        # Existing deterministic application outcomes that also prove a server response.
        ResultCode.ALREADY_EXISTS,
        ResultCode.OPERATION_IN_PROGRESS,
    }
)


def classify_validated_mutation_result(
    result_code: ResultCode,
) -> MutationDeliveryClassification:
    """Classify only an already schema-validated mutation response."""

    if result_code in _KNOWN_SUCCESS_CODES:
        return MutationDeliveryClassification.KNOWN_SUCCESS
    if result_code in _KNOWN_FAILURE_CODES:
        return MutationDeliveryClassification.KNOWN_FAILURE
    return MutationDeliveryClassification.UNKNOWN_COMMIT


def classify_application_mutation_result(*, ok: bool, code: str) -> MutationDeliveryClassification:
    """Apply the same closed delivery semantics at a direct Application boundary."""

    if ok:
        return MutationDeliveryClassification.KNOWN_SUCCESS
    if code in {
        "ticket_not_found",
        "version_conflict",
        "permission_denied",
        "operator_not_authorized",
        "idempotency_payload_conflict",
        "idempotency_request_in_progress",
        "validation_error",
    }:
        return MutationDeliveryClassification.KNOWN_FAILURE
    return MutationDeliveryClassification.UNKNOWN_COMMIT

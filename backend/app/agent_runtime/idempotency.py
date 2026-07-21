"""Stable logical-operation identity for replay-safe MCP mutations."""

from uuid import UUID

from app.agent.enums import PendingAction
from app.agent.state import PendingOperation
from app.policy.normalization import stable_json_hash


def build_pending_operation(
    *,
    thread_id: UUID,
    intent_version: int,
    action: PendingAction,
    payload: object,
    target_id: UUID | None,
    expected_ticket_version: int | None,
    expected_appointment_version: int | None = None,
) -> PendingOperation:
    material = {
        "thread_id": str(thread_id),
        "intent_version": intent_version,
        "action": action.value,
        "payload": payload,
        "target_id": str(target_id) if target_id else None,
        "expected_ticket_version": expected_ticket_version,
        "expected_appointment_version": expected_appointment_version,
    }
    fingerprint = stable_json_hash(material)
    return PendingOperation(
        action=action,
        request_fingerprint=fingerprint,
        idempotency_key=f"agent-{fingerprint}",
        intent_version=intent_version,
        expected_ticket_version=expected_ticket_version,
        expected_appointment_version=expected_appointment_version,
    )

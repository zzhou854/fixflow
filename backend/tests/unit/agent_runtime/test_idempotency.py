"""Replay keys are stable logical-operation identities, not request attempts."""

from uuid import UUID, uuid4

import pytest
from app.agent.enums import PendingAction
from app.agent.state import PendingOperation
from app.agent_runtime.idempotency import build_pending_operation


def _operation(
    action: PendingAction = PendingAction.BOOK_APPOINTMENT,
    *,
    thread_id: UUID | None = None,
    target_id: UUID | None = None,
    intent_version: int = 3,
    payload: object = "2026-07-21T13:00:00Z",
    ticket_version: int = 4,
    appointment_version: int = 2,
) -> PendingOperation:
    return build_pending_operation(
        thread_id=thread_id or uuid4(),
        intent_version=intent_version,
        action=action,
        payload={"slot": payload},
        target_id=target_id or uuid4(),
        expected_ticket_version=ticket_version,
        expected_appointment_version=appointment_version,
    )


@pytest.mark.parametrize(
    "action",
    [
        PendingAction.CREATE_TICKET,
        PendingAction.BOOK_APPOINTMENT,
        PendingAction.RESCHEDULE_APPOINTMENT,
        PendingAction.REQUEST_HUMAN_REVIEW,
    ],
)
def test_mutation_keys_are_stable_per_logical_operation(action: PendingAction) -> None:
    thread_id, target_id = uuid4(), uuid4()
    first = _operation(action, thread_id=thread_id, target_id=target_id)
    replay_after_new_trace_or_retry = _operation(action, thread_id=thread_id, target_id=target_id)
    assert first.idempotency_key == replay_after_new_trace_or_retry.idempotency_key
    assert first.request_fingerprint == replay_after_new_trace_or_retry.request_fingerprint


@pytest.mark.parametrize(
    "changed",
    [
        {"payload": "2026-07-21T13:30:00Z"},
        {"ticket_version": 5},
        {"appointment_version": 3},
        {"intent_version": 4},
    ],
)
def test_mutation_keys_change_when_the_logical_operation_changes(
    changed: dict[str, int | str],
) -> None:
    thread_id, target_id = uuid4(), uuid4()
    original = _operation(thread_id=thread_id, target_id=target_id)
    if "payload" in changed:
        changed_operation = _operation(
            thread_id=thread_id, target_id=target_id, payload=changed["payload"]
        )
    elif "ticket_version" in changed:
        changed_operation = _operation(
            thread_id=thread_id, target_id=target_id, ticket_version=int(changed["ticket_version"])
        )
    elif "appointment_version" in changed:
        changed_operation = _operation(
            thread_id=thread_id,
            target_id=target_id,
            appointment_version=int(changed["appointment_version"]),
        )
    else:
        changed_operation = _operation(
            thread_id=thread_id, target_id=target_id, intent_version=int(changed["intent_version"])
        )
    assert original.idempotency_key != changed_operation.idempotency_key

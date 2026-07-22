from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from app.agent_runtime.mcp.delivery import (
    MutationDeliveryClassification,
    classify_validated_mutation_result,
)
from app.domain.enums import ActorType
from app.fault_injection import FaultAction, FaultPoint, NoOpFaultInjector, ScriptedFaultInjector
from app.fault_injection.injector import InjectedFault
from app.infrastructure.database.models.reconciliation import ReconciliationAction
from app.property_operations.contracts.common import ResultCode
from app.reconciliation.models import CreateCase
from app.reconciliation.validators import (
    validate_book_appointment,
    validate_create_ticket,
    validate_escalate_ticket,
    validate_reschedule_appointment,
)


@dataclass
class Entity:
    version: int


def command(operation_id: UUID | None = None) -> CreateCase:
    return CreateCase(
        operation_id=operation_id or uuid4(),
        action=ReconciliationAction.CREATE_TICKET,
        thread_id=uuid4(),
        original_run_id=uuid4(),
        original_trace_id=uuid4(),
        operation_idempotency_fingerprint="a" * 64,
        request_fingerprint="b" * 64,
        actor_type=ActorType.RESIDENT,
        actor_id=uuid4(),
        user_id=uuid4(),
        property_id=uuid4(),
    )


def test_case_key_is_deterministic_and_excludes_runtime_attempt_identity() -> None:
    item = command()
    assert item.case_key == item.model_copy().case_key
    assert len(item.case_key) == 64


def test_case_key_changes_with_request_fingerprint() -> None:
    item = command()
    assert item.case_key != item.model_copy(update={"request_fingerprint": "c" * 64}).case_key


@pytest.mark.asyncio
async def test_noop_fault_injector_never_fails() -> None:
    await NoOpFaultInjector().hit(uuid4(), FaultPoint.BEFORE_MCP_SEND)


@pytest.mark.asyncio
async def test_scripted_fault_is_operation_point_and_count_scoped() -> None:
    operation = uuid4()
    injector = ScriptedFaultInjector(
        {(operation, FaultPoint.AFTER_MCP_SEND, 2): FaultAction.TIMEOUT}
    )
    await injector.hit(operation, FaultPoint.AFTER_MCP_SEND)
    with pytest.raises(InjectedFault):
        await injector.hit(operation, FaultPoint.AFTER_MCP_SEND)
    await injector.hit(uuid4(), FaultPoint.AFTER_MCP_SEND)


@pytest.mark.parametrize("code", [ResultCode.CREATED, ResultCode.UPDATED])
def test_validated_success_codes_are_known_success(code: ResultCode) -> None:
    assert classify_validated_mutation_result(code) is MutationDeliveryClassification.KNOWN_SUCCESS


@pytest.mark.parametrize(
    "code",
    [
        ResultCode.PERMISSION_DENIED,
        ResultCode.NOT_FOUND,
        ResultCode.VERSION_CONFLICT,
        ResultCode.TIME_CONFLICT,
        ResultCode.IDEMPOTENCY_CONFLICT,
        ResultCode.VALIDATION_ERROR,
        ResultCode.UNSUPPORTED_OPERATION,
    ],
)
def test_explicit_business_errors_are_known_failures(code: ResultCode) -> None:
    assert classify_validated_mutation_result(code) is MutationDeliveryClassification.KNOWN_FAILURE


@pytest.mark.parametrize(
    ("validator", "event_type"),
    [
        (validate_create_ticket, "ticket.created"),
        (validate_book_appointment, "appointment.booked"),
        (validate_reschedule_appointment, "appointment.rescheduled"),
        (validate_escalate_ticket, "ticket.escalated"),
    ],
)
def test_action_validators_require_version_and_action_specific_outbox(
    validator: Callable[[Entity, dict[str, object], set[str]], bool], event_type: str
) -> None:
    assert validator(Entity(2), {"resource_version": 2}, {event_type})
    assert not validator(Entity(3), {"resource_version": 2}, {event_type})
    assert not validator(Entity(2), {"resource_version": 2}, {"unrelated.event"})

"""Authoritative reconciliation evidence over real PostgreSQL business rows."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from app.application.models import CreateTicketCommand, MutationMetadata
from app.application.ports import UnitOfWork
from app.application.services import FixFlowApplicationService
from app.domain.enums import ActorType, IssueCategory, Severity
from app.infrastructure.database.models import (
    IdempotencyRecord,
    OutboxEvent,
    Property,
    ResidentPropertyRelation,
    User,
)
from app.infrastructure.database.models.reconciliation import (
    ReconciliationAction,
    ReconciliationStatus,
)
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.reconciliation.evidence import (
    OperationOutcomePermissionDenied,
    SqlAlchemyOperationOutcomeQuery,
)
from app.reconciliation.models import ClaimedCase, OutcomeStatus
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mcp_server.schemas.appointments import BookAppointmentRequest, RescheduleAppointmentRequest
from mcp_server.schemas.tickets import (
    CreateRepairTicketRequest,
    EscalateToOperatorRequest,
    EscalationReasonCode,
)


@pytest_asyncio.fixture
async def evidence_env(
    migrated_database_url: str,
) -> AsyncIterator[tuple[async_sessionmaker[AsyncSession], FixFlowApplicationService, UUID, UUID]]:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    resident_id, property_id = uuid4(), uuid4()
    async with sessions.begin() as session:
        session.add_all(
            [
                User(
                    id=resident_id,
                    username=f"reconciliation-{resident_id}",
                    password_hash="not-plaintext",
                    role="RESIDENT",
                ),
                Property(
                    id=property_id,
                    community_name=f"reconciliation-{property_id}",
                    building_no="1",
                    unit_no="1",
                    room_no=property_id.hex[:8],
                    address_text="reconciliation integration property",
                ),
                ResidentPropertyRelation(resident_id=resident_id, property_id=property_id),
            ]
        )

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    try:
        yield sessions, FixFlowApplicationService(uow_factory), resident_id, property_id
    finally:
        await engine.dispose()


def _case(
    *,
    operation_id: UUID,
    resident_id: UUID,
    property_id: UUID,
    request_fingerprint: str,
    target_entity_id: UUID | None = None,
    action: ReconciliationAction = ReconciliationAction.CREATE_TICKET,
    actor_type: ActorType = ActorType.RESIDENT,
) -> ClaimedCase:
    now = datetime.now(UTC)
    return ClaimedCase(
        id=uuid4(),
        operation_id=operation_id,
        action=action,
        thread_id=uuid4(),
        property_id=property_id,
        target_entity_id=target_entity_id or property_id,
        status=ReconciliationStatus.PROCESSING,
        last_evidence_status=None,
        attempt_count=1,
        available_at=now,
        resolution_code=None,
        safe_result=None,
        created_at=now,
        updated_at=now,
        claimed_by="evidence-worker",
        claim_token=uuid4(),
        original_run_id=uuid4(),
        original_trace_id=uuid4(),
        request_fingerprint=request_fingerprint,
        actor_type=actor_type,
        actor_id=resident_id,
        user_id=resident_id,
    )


@pytest.mark.asyncio
async def test_authoritative_query_distinguishes_committed_absent_and_conflict(
    evidence_env: tuple[async_sessionmaker[AsyncSession], FixFlowApplicationService, UUID, UUID],
) -> None:
    sessions, application, resident_id, property_id = evidence_env
    operation_id = uuid4()
    fingerprint = "a" * 64
    result = await application.create_ticket(
        CreateTicketCommand(
            metadata=MutationMetadata(
                actor_type=ActorType.RESIDENT,
                actor_id=resident_id,
                trace_id=uuid4(),
                idempotency_key="evidence-committed",
                occurred_at=datetime.now(UTC),
                operation_id=operation_id,
                request_fingerprint=fingerprint,
            ),
            resident_id=resident_id,
            property_id=property_id,
            issue_category=IssueCategory.WATER_LEAK,
            issue_location="kitchen sink",
            issue_description="pipe leak",
            severity=Severity.MEDIUM,
        )
    )
    assert result.ok
    query = SqlAlchemyOperationOutcomeQuery(sessions)
    subject = _case(
        operation_id=operation_id,
        resident_id=resident_id,
        property_id=property_id,
        request_fingerprint=fingerprint,
    )
    committed = await query.get_operation_outcome(subject)
    assert committed.status is OutcomeStatus.COMMITTED
    assert committed.safe_result is not None
    assert committed.safe_result["resource_id"] == str(result.resource_id)

    absent = await query.get_operation_outcome(
        _case(
            operation_id=uuid4(),
            resident_id=resident_id,
            property_id=property_id,
            request_fingerprint="b" * 64,
        )
    )
    assert absent.status is OutcomeStatus.NOT_COMMITTED

    async with sessions.begin() as session:
        await session.execute(delete(OutboxEvent).where(OutboxEvent.operation_id == operation_id))
    inconsistent = await query.get_operation_outcome(subject)
    assert inconsistent.status is OutcomeStatus.INCONSISTENT
    assert inconsistent.reason_code == "EVIDENCE_CONFLICT"


@pytest.mark.asyncio
async def test_authoritative_query_rejects_identity_without_property_authority(
    evidence_env: tuple[async_sessionmaker[AsyncSession], FixFlowApplicationService, UUID, UUID],
) -> None:
    sessions, _, resident_id, property_id = evidence_env
    subject = _case(
        operation_id=uuid4(),
        resident_id=resident_id,
        property_id=property_id,
        request_fingerprint="c" * 64,
    ).model_copy(update={"actor_id": uuid4()})
    with pytest.raises(OperationOutcomePermissionDenied):
        await SqlAlchemyOperationOutcomeQuery(sessions).get_operation_outcome(subject)


@pytest.mark.asyncio
async def test_all_four_mutations_have_authoritative_committed_evidence(mcp_env: Any) -> None:
    """Every formal mutation is recoverable from primary PostgreSQL evidence."""

    env = mcp_env
    query = SqlAlchemyOperationOutcomeQuery(env.sessions)

    async def assert_committed(
        *,
        key: str,
        action: ReconciliationAction,
        actor_id: UUID,
        property_id: UUID,
        fingerprint: str,
        target_entity_id: UUID,
        expected_resource_id: UUID,
    ) -> None:
        scope = {
            ReconciliationAction.CREATE_TICKET: "create_ticket",
            ReconciliationAction.BOOK_APPOINTMENT: "book_appointment",
            ReconciliationAction.RESCHEDULE_APPOINTMENT: "reschedule_appointment",
            ReconciliationAction.ESCALATE_TO_OPERATOR: "escalate_ticket",
        }[action]
        async with env.sessions() as session:
            record = await session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.scope == scope,
                    IdempotencyRecord.actor_id == str(actor_id),
                    IdempotencyRecord.idempotency_key == key,
                )
            )
        assert record is not None
        outcome = await query.get_operation_outcome(
            _case(
                operation_id=record.operation_id,
                resident_id=actor_id,
                property_id=property_id,
                request_fingerprint=fingerprint,
                target_entity_id=target_entity_id,
                action=action,
                actor_type=(
                    ActorType.OPERATOR
                    if action is ReconciliationAction.ESCALATE_TO_OPERATOR
                    else ActorType.RESIDENT
                ),
            )
        )
        assert outcome.status is OutcomeStatus.COMMITTED
        assert outcome.safe_result is not None
        assert outcome.safe_result["resource_id"] == str(expected_resource_id)

    create_key, create_operation, create_fingerprint = (
        f"evidence-create-{uuid4()}",
        uuid4(),
        "1" * 64,
    )
    created = await env.adapter.create_repair_ticket(
        CreateRepairTicketRequest(
            actor_type=ActorType.RESIDENT,
            actor_id=env.resident_id,
            trace_id=uuid4(),
            idempotency_key=create_key,
            operation_id=create_operation,
            request_fingerprint=create_fingerprint,
            resident_id=env.resident_id,
            property_id=env.property_id,
            issue_category=IssueCategory.WATER_LEAK,
            issue_location=f"reconciliation-{uuid4()}",
            issue_description="authoritative evidence test",
            severity=Severity.MEDIUM,
        )
    )
    assert created.data is not None
    ticket_id = created.data.resource_id
    await assert_committed(
        key=create_key,
        action=ReconciliationAction.CREATE_TICKET,
        actor_id=env.resident_id,
        property_id=env.property_id,
        fingerprint=create_fingerprint,
        target_entity_id=env.property_id,
        expected_resource_id=ticket_id,
    )

    book_key, book_fingerprint = f"evidence-book-{uuid4()}", "2" * 64
    booked = await env.adapter.book_appointment(
        BookAppointmentRequest(
            actor_type=ActorType.RESIDENT,
            actor_id=env.resident_id,
            trace_id=uuid4(),
            idempotency_key=book_key,
            operation_id=uuid4(),
            request_fingerprint=book_fingerprint,
            ticket_id=ticket_id,
            worker_id=env.worker_ids[0],
            scheduled_start=env.slot,
            scheduled_end=env.slot + timedelta(hours=1),
            expected_version=1,
        )
    )
    assert booked.data is not None
    appointment_id = booked.data.resource_id
    await assert_committed(
        key=book_key,
        action=ReconciliationAction.BOOK_APPOINTMENT,
        actor_id=env.resident_id,
        property_id=env.property_id,
        fingerprint=book_fingerprint,
        target_entity_id=ticket_id,
        expected_resource_id=appointment_id,
    )

    reschedule_key, reschedule_fingerprint = f"evidence-reschedule-{uuid4()}", "3" * 64
    rescheduled = await env.adapter.reschedule_appointment(
        RescheduleAppointmentRequest(
            actor_type=ActorType.RESIDENT,
            actor_id=env.resident_id,
            trace_id=uuid4(),
            idempotency_key=reschedule_key,
            operation_id=uuid4(),
            request_fingerprint=reschedule_fingerprint,
            ticket_id=ticket_id,
            appointment_id=appointment_id,
            worker_id=env.worker_ids[1],
            scheduled_start=env.slot + timedelta(hours=2),
            scheduled_end=env.slot + timedelta(hours=3),
            expected_version=2,
            expected_appointment_version=1,
        )
    )
    assert rescheduled.data is not None
    replacement_id = rescheduled.data.resource_id
    await assert_committed(
        key=reschedule_key,
        action=ReconciliationAction.RESCHEDULE_APPOINTMENT,
        actor_id=env.resident_id,
        property_id=env.property_id,
        fingerprint=reschedule_fingerprint,
        target_entity_id=appointment_id,
        expected_resource_id=replacement_id,
    )

    escalate_key, escalate_fingerprint = f"evidence-escalate-{uuid4()}", "4" * 64
    escalated = await env.adapter.escalate_to_operator(
        EscalateToOperatorRequest(
            actor_type=ActorType.OPERATOR,
            actor_id=env.operator_id,
            trace_id=uuid4(),
            idempotency_key=escalate_key,
            operation_id=uuid4(),
            request_fingerprint=escalate_fingerprint,
            ticket_id=ticket_id,
            expected_version=3,
            reason_code=EscalationReasonCode.MANUAL_REVIEW,
            reason_text="authoritative reconciliation review",
            evidence=("integration-test-evidence",),
        )
    )
    assert escalated.data is not None
    await assert_committed(
        key=escalate_key,
        action=ReconciliationAction.ESCALATE_TO_OPERATOR,
        actor_id=env.operator_id,
        property_id=env.property_id,
        fingerprint=escalate_fingerprint,
        target_entity_id=ticket_id,
        expected_resource_id=ticket_id,
    )

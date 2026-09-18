"""Real PostgreSQL transaction, idempotency, and concurrency acceptance tests."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from app.application.errors import ActiveAppointmentExists
from app.application.models import (
    BookAppointmentCommand,
    CreateTicketCommand,
    EscalateTicketCommand,
    MutationMetadata,
    RecordWorkerEventCommand,
    RecoverTicketCommand,
    RescheduleAppointmentCommand,
    ReviewRepairCommand,
)
from app.application.ports import StoredIdempotency, UnitOfWork
from app.application.service_support import _request_hash
from app.application.services import FixFlowApplicationService
from app.domain.enums import (
    AcceptanceRejectionReason,
    ActorType,
    AppointmentPurpose,
    AppointmentStatus,
    CancellationReason,
    EscalationDisposition,
    FailureReason,
    IssueCategory,
    Severity,
    TicketStatus,
    WorkerEventType,
    WorkerSkillType,
)
from app.domain.models import AppointmentDraft, AppointmentSnapshot, TicketSnapshot
from app.infrastructure.database.models import (
    Appointment,
    AppointmentStatusHistory,
    IdempotencyRecord,
    OutboxEvent,
    Property,
    RepairTicket,
    ResidentPropertyRelation,
    TicketStatusHistory,
    User,
    Worker,
    WorkerAvailability,
    WorkerEvent,
    WorkerSkill,
)
from app.infrastructure.database.models.idempotency import IdempotencyExecutionStatus
from app.infrastructure.database.repositories import (
    SqlAlchemyAppointmentRepository,
    SqlAlchemyIdempotencyRepository,
    SqlAlchemyTicketRepository,
)
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql.ranges import Range
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


class AsyncCompetitionBarrier:
    """Release exactly two independent sessions at a named competition edge."""

    def __init__(self) -> None:
        self._arrived = 0
        self._lock = asyncio.Lock()
        self._release = asyncio.Event()
        self.session_ids: set[int] = set()

    async def wait(self, session: AsyncSession) -> None:
        async with self._lock:
            self.session_ids.add(id(session))
            self._arrived += 1
            if self._arrived == 2:
                self._release.set()
        await asyncio.wait_for(self._release.wait(), timeout=5)


@dataclass(slots=True)
class ApplicationEnvironment:
    service: FixFlowApplicationService
    sessions: async_sessionmaker[AsyncSession]
    resident_id: UUID
    operator_id: UUID
    property_id: UUID
    worker_ids: tuple[UUID, UUID]
    slot: datetime


@pytest_asyncio.fixture
async def application_env(
    migrated_database_url: str,
) -> AsyncIterator[ApplicationEnvironment]:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    resident_id = uuid4()
    operator_id = uuid4()
    property_id = uuid4()
    worker_ids = (uuid4(), uuid4())
    slot = datetime(2030, 1, 10, 9, tzinfo=UTC)
    async with sessions.begin() as session:
        session.add_all(
            [
                User(
                    id=resident_id,
                    username=f"resident-{resident_id}",
                    password_hash="not-plaintext",
                    role="RESIDENT",
                ),
                User(
                    id=operator_id,
                    username=f"operator-{operator_id}",
                    password_hash="not-plaintext",
                    role="OPERATOR",
                ),
                Property(
                    id=property_id,
                    community_name=f"Task4-{property_id}",
                    building_no="1",
                    unit_no="1",
                    room_no=property_id.hex[:8],
                    address_text="task 4 integration property",
                ),
                ResidentPropertyRelation(
                    resident_id=resident_id,
                    property_id=property_id,
                ),
            ]
        )
        for index, worker_id in enumerate(worker_ids):
            session.add(
                Worker(
                    id=worker_id,
                    name=f"worker-{index}-{worker_id}",
                    service_area=f"Task4-{property_id}",
                )
            )
            session.add(WorkerSkill(worker_id=worker_id, skill_type=WorkerSkillType.PLUMBING))
            session.add(
                WorkerAvailability(
                    worker_id=worker_id,
                    available_range=Range(
                        slot - timedelta(days=1), slot + timedelta(days=30), bounds="[)"
                    ),
                )
            )

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    service = FixFlowApplicationService(uow_factory)
    environment = ApplicationEnvironment(
        service=service,
        sessions=sessions,
        resident_id=resident_id,
        operator_id=operator_id,
        property_id=property_id,
        worker_ids=worker_ids,
        slot=slot,
    )
    try:
        yield environment
    finally:
        await engine.dispose()


def _metadata(
    env: ApplicationEnvironment,
    *,
    actor_type: ActorType = ActorType.RESIDENT,
    actor_id: UUID | None = None,
    key: str | None = None,
    occurred_at: datetime | None = None,
) -> MutationMetadata:
    return MutationMetadata(
        actor_type=actor_type,
        actor_id=actor_id or env.resident_id,
        trace_id=uuid4(),
        idempotency_key=key or uuid4().hex,
        occurred_at=occurred_at or env.slot,
    )


async def _create_ticket(env: ApplicationEnvironment, *, location: str | None = None) -> UUID:
    result = await env.service.create_ticket(
        CreateTicketCommand(
            metadata=_metadata(env),
            resident_id=env.resident_id,
            property_id=env.property_id,
            issue_category=IssueCategory.WATER_LEAK,
            issue_location=location or f"kitchen-{uuid4()}",
            issue_description="pipe is leaking",
            severity=Severity.MEDIUM,
        )
    )
    assert result.ok
    assert result.resource_id is not None
    return result.resource_id


async def _book(
    env: ApplicationEnvironment,
    ticket_id: UUID,
    *,
    worker_id: UUID | None = None,
    starts_at: datetime | None = None,
    expected_ticket_version: int = 1,
) -> UUID:
    start = starts_at or env.slot
    result = await env.service.book_appointment(
        BookAppointmentCommand(
            metadata=_metadata(env),
            ticket_id=ticket_id,
            worker_id=worker_id or env.worker_ids[0],
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            expected_ticket_version=expected_ticket_version,
        )
    )
    assert result.ok, result
    assert result.resource_id is not None
    return result.resource_id


async def _complete_repair(
    env: ApplicationEnvironment, ticket_id: UUID, appointment_id: UUID
) -> None:
    ticket_version = 2
    for index, event_type in enumerate(
        (
            WorkerEventType.ACCEPTED,
            WorkerEventType.DEPARTED,
            WorkerEventType.ARRIVED,
            WorkerEventType.STARTED,
            WorkerEventType.COMPLETED,
        )
    ):
        result = await env.service.record_worker_event(
            RecordWorkerEventCommand(
                metadata=_metadata(
                    env,
                    actor_type=ActorType.WORKER,
                    actor_id=env.worker_ids[0],
                    occurred_at=env.slot + timedelta(minutes=index),
                ),
                ticket_id=ticket_id,
                appointment_id=appointment_id,
                subject_worker_id=env.worker_ids[0],
                event_type=event_type,
                external_event_key=f"{event_type.value}-{uuid4()}",
                expected_ticket_version=ticket_version,
                expected_appointment_version=1,
            )
        )
        assert result.ok, result
        if event_type is WorkerEventType.STARTED:
            ticket_version = 3


@pytest.mark.asyncio
async def test_ticket_and_outbox_commit_atomically_and_replay_has_no_duplicate_event(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    command = CreateTicketCommand(
        metadata=_metadata(env, key=f"outbox-{uuid4().hex}"),
        resident_id=env.resident_id,
        property_id=env.property_id,
        issue_category=IssueCategory.WATER_LEAK,
        issue_location=f"outbox-{uuid4().hex}",
        issue_description="transactional outbox acceptance",
        severity=Severity.MEDIUM,
    )
    first = await env.service.create_ticket(command)
    replay = await env.service.create_ticket(command)
    assert first.ok and replay.replayed
    async with env.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(
                    OutboxEvent.aggregate_id == first.resource_id,
                    OutboxEvent.event_type == "ticket.created",
                )
            )
            == 1
        )


@pytest.mark.asyncio
async def test_booking_reschedule_and_escalation_emit_closed_outbox_events(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    appointment_id = await _book(env, ticket_id)
    rescheduled = await env.service.reschedule_appointment(
        RescheduleAppointmentCommand(
            metadata=_metadata(env),
            ticket_id=ticket_id,
            appointment_id=appointment_id,
            worker_id=env.worker_ids[0],
            starts_at=env.slot + timedelta(hours=2),
            ends_at=env.slot + timedelta(hours=3),
            expected_ticket_version=2,
            expected_appointment_version=1,
        )
    )
    assert rescheduled.ok
    escalated = await env.service.escalate_ticket(
        EscalateTicketCommand(
            metadata=_metadata(env, actor_type=ActorType.OPERATOR, actor_id=env.operator_id),
            ticket_id=ticket_id,
            expected_ticket_version=3,
            reason_code="OPERATOR_REVIEW",
            reason_text="requires controlled review",
            evidence=("operator-reviewed",),
        )
    )
    assert escalated.ok
    async with env.sessions() as session:
        event_types = set(
            await session.scalars(
                select(OutboxEvent.event_type).where(
                    (OutboxEvent.aggregate_id == ticket_id)
                    | (OutboxEvent.payload["ticket_id"].as_string() == str(ticket_id))
                )
            )
        )
    assert {
        "ticket.created",
        "ticket.status_changed",
        "appointment.booked",
        "appointment.rescheduled",
        "ticket.escalated",
    } <= event_types


@pytest.mark.asyncio
async def test_rejected_business_mutation_writes_no_outbox_event(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    trace_id = uuid4()
    result = await env.service.create_ticket(
        CreateTicketCommand(
            metadata=replace(_metadata(env), trace_id=trace_id),
            resident_id=uuid4(),
            property_id=env.property_id,
            issue_category=IssueCategory.WATER_LEAK,
            issue_location="unauthorized",
            issue_description="must roll back",
            severity=Severity.MEDIUM,
        )
    )
    assert not result.ok
    async with env.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.trace_id == trace_id)
            )
            == 0
        )


async def _start_repair(env: ApplicationEnvironment, ticket_id: UUID, appointment_id: UUID) -> None:
    for event_type in (
        WorkerEventType.ACCEPTED,
        WorkerEventType.DEPARTED,
        WorkerEventType.ARRIVED,
        WorkerEventType.STARTED,
    ):
        result = await env.service.record_worker_event(
            RecordWorkerEventCommand(
                metadata=_metadata(
                    env,
                    actor_type=ActorType.WORKER,
                    actor_id=env.worker_ids[0],
                ),
                ticket_id=ticket_id,
                appointment_id=appointment_id,
                subject_worker_id=env.worker_ids[0],
                event_type=event_type,
                external_event_key=f"{event_type.value}-{uuid4()}",
                expected_ticket_version=2,
                expected_appointment_version=1,
            )
        )
        assert result.ok, result


@pytest.mark.asyncio
async def test_complete_worker_chain_then_resident_accepts_and_closes(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    location = f"kitchen-recurrence-{uuid4()}"
    ticket_id = await _create_ticket(env, location=location)
    appointment_id = await _book(env, ticket_id)
    await _complete_repair(env, ticket_id, appointment_id)
    accepted = await env.service.review_repair(
        ReviewRepairCommand(
            metadata=_metadata(env, occurred_at=env.slot + timedelta(hours=2)),
            ticket_id=ticket_id,
            expected_ticket_version=4,
            accepted=True,
        )
    )

    assert accepted.ok
    recurrence_id = await _create_ticket(env, location=location)
    assert recurrence_id != ticket_id
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment = await session.get(Appointment, appointment_id)
        events = list(
            await session.scalars(
                select(WorkerEvent)
                .where(WorkerEvent.appointment_id == appointment_id)
                .order_by(WorkerEvent.sequence_no)
            )
        )
        ticket_history = list(
            await session.scalars(
                select(TicketStatusHistory)
                .where(TicketStatusHistory.ticket_id == ticket_id)
                .order_by(TicketStatusHistory.version_after)
            )
        )
        appointment_history = list(
            await session.scalars(
                select(AppointmentStatusHistory)
                .where(AppointmentStatusHistory.appointment_id == appointment_id)
                .order_by(AppointmentStatusHistory.version_after)
            )
        )
    assert ticket is not None and appointment is not None
    assert (ticket.status, ticket.version, ticket.closed_at) == (
        TicketStatus.CLOSED,
        5,
        env.slot + timedelta(hours=2),
    )
    assert (appointment.status, appointment.version) == (AppointmentStatus.FULFILLED, 2)
    assert [event.sequence_no for event in events] == [1, 2, 3, 4, 5]
    assert [item.action for item in ticket_history] == [
        "CREATE",
        "BOOK_APPOINTMENT",
        "START_WORK",
        "COMPLETE_WORK",
        "RESIDENT_ACCEPT",
    ]
    assert [item.to_status for item in appointment_history] == [
        AppointmentStatus.BOOKED,
        AppointmentStatus.FULFILLED,
    ]
    assert all(item.trace_id.int != 0 for item in ticket_history + appointment_history)


@pytest.mark.asyncio
async def test_resident_can_close_scheduled_ticket_after_onsite_repair(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env, location=f"resident-confirm-{uuid4()}")
    appointment_id = await _book(env, ticket_id)

    accepted = await env.service.review_repair(
        ReviewRepairCommand(
            metadata=_metadata(env, occurred_at=env.slot + timedelta(hours=1)),
            ticket_id=ticket_id,
            expected_ticket_version=2,
            expected_appointment_version=1,
            accepted=True,
        )
    )

    assert accepted.ok
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment = await session.get(Appointment, appointment_id)
        appointment_history = list(
            await session.scalars(
                select(AppointmentStatusHistory)
                .where(AppointmentStatusHistory.appointment_id == appointment_id)
                .order_by(AppointmentStatusHistory.version_after)
            )
        )
    assert ticket is not None and appointment is not None
    assert (ticket.status, ticket.version) == (TicketStatus.CLOSED, 3)
    assert (appointment.status, appointment.version) == (AppointmentStatus.FULFILLED, 2)
    assert appointment_history[-1].reason_code == "RESIDENT_CONFIRMED_COMPLETE"


@pytest.mark.asyncio
async def test_resident_cannot_close_scheduled_ticket_before_visit_starts(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env, location=f"future-confirm-{uuid4()}")
    appointment_id = await _book(env, ticket_id)

    accepted = await env.service.review_repair(
        ReviewRepairCommand(
            metadata=_metadata(env, occurred_at=env.slot - timedelta(minutes=1)),
            ticket_id=ticket_id,
            expected_ticket_version=2,
            expected_appointment_version=1,
            accepted=True,
        )
    )

    assert not accepted.ok
    assert accepted.code == "appointment_not_started"
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment = await session.get(Appointment, appointment_id)
    assert ticket is not None and ticket.status is TicketStatus.SCHEDULED
    assert appointment is not None and appointment.status is AppointmentStatus.BOOKED


@pytest.mark.asyncio
async def test_initial_worker_rejection_returns_ticket_to_open(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    appointment_id = await _book(env, ticket_id)
    rejected = await env.service.record_worker_event(
        RecordWorkerEventCommand(
            metadata=_metadata(env, actor_type=ActorType.WORKER, actor_id=env.worker_ids[0]),
            ticket_id=ticket_id,
            appointment_id=appointment_id,
            subject_worker_id=env.worker_ids[0],
            event_type=WorkerEventType.REJECTED,
            external_event_key=f"rejected-{uuid4()}",
            expected_ticket_version=2,
            expected_appointment_version=1,
            cancellation_reason=CancellationReason.WORKER_REJECTED,
            reason_text="cannot take this appointment",
            evidence=("worker-note",),
        )
    )

    assert rejected.ok
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment = await session.get(Appointment, appointment_id)
    assert ticket is not None and appointment is not None
    assert (ticket.status, ticket.rework_count) == (TicketStatus.OPEN, 0)
    assert appointment.status is AppointmentStatus.CANCELLED
    assert appointment.outcome_reason_code == CancellationReason.WORKER_REJECTED.value


@pytest.mark.asyncio
async def test_rework_worker_rejection_preserves_rework_fact(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    first_appointment_id = await _book(env, ticket_id)
    await _complete_repair(env, ticket_id, first_appointment_id)
    review = await env.service.review_repair(
        ReviewRepairCommand(
            metadata=_metadata(env),
            ticket_id=ticket_id,
            expected_ticket_version=4,
            accepted=False,
            rejection_reason=AcceptanceRejectionReason.ISSUE_NOT_RESOLVED,
            explanation="still leaking",
        )
    )
    assert review.ok
    rework_appointment_id = await _book(
        env,
        ticket_id,
        starts_at=env.slot + timedelta(days=2),
        expected_ticket_version=5,
    )
    rejected = await env.service.record_worker_event(
        RecordWorkerEventCommand(
            metadata=_metadata(env, actor_type=ActorType.WORKER, actor_id=env.worker_ids[0]),
            ticket_id=ticket_id,
            appointment_id=rework_appointment_id,
            subject_worker_id=env.worker_ids[0],
            event_type=WorkerEventType.REJECTED,
            external_event_key=f"rework-rejected-{uuid4()}",
            expected_ticket_version=6,
            expected_appointment_version=1,
            cancellation_reason=CancellationReason.WORKER_REJECTED,
            reason_text="cannot take rework slot",
            evidence=("worker-note",),
        )
    )

    assert rejected.ok
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment = await session.get(Appointment, rework_appointment_id)
    assert ticket is not None and appointment is not None
    assert (ticket.status, ticket.rework_count, ticket.version) == (
        TicketStatus.REWORK_REQUIRED,
        1,
        7,
    )
    assert (appointment.purpose, appointment.status) == (
        AppointmentPurpose.REWORK,
        AppointmentStatus.CANCELLED,
    )


@pytest.mark.asyncio
async def test_create_ticket_idempotency_replays_and_rejects_changed_payload(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    metadata = _metadata(env, key="same-create-key")
    command = CreateTicketCommand(
        metadata=metadata,
        resident_id=env.resident_id,
        property_id=env.property_id,
        issue_category=IssueCategory.WATER_LEAK,
        issue_location=f"bathroom-{uuid4()}",
        issue_description="leak under sink",
        severity=Severity.MEDIUM,
    )
    first = await env.service.create_ticket(command)
    replay = await env.service.create_ticket(command)
    conflict = await env.service.create_ticket(
        replace(command, issue_description="different request body")
    )

    assert first.ok and replay.ok and replay.replayed
    assert first.resource_id == replay.resource_id
    assert conflict.code == "idempotency_payload_conflict"
    async with env.sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(RepairTicket)
            .where(RepairTicket.id == first.resource_id)
        )
    assert count == 1


@pytest.mark.asyncio
async def test_concurrent_same_idempotency_key_commits_one_business_effect(
    application_env: ApplicationEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = application_env
    key = f"concurrent-create-{uuid4()}"
    first = CreateTicketCommand(
        metadata=_metadata(env, key=key, occurred_at=env.slot),
        resident_id=env.resident_id,
        property_id=env.property_id,
        issue_category=IssueCategory.WATER_LEAK,
        issue_location=f"bathroom-{uuid4()}",
        issue_description="tap is leaking",
        severity=Severity.MEDIUM,
    )
    second = replace(
        first,
        metadata=_metadata(env, key=key, occurred_at=env.slot + timedelta(minutes=5)),
    )
    barrier = AsyncCompetitionBarrier()
    original_acquire = SqlAlchemyIdempotencyRepository.acquire

    async def synchronized_acquire(
        repository: SqlAlchemyIdempotencyRepository,
        *,
        scope: str,
        actor_type: ActorType,
        actor_id: UUID,
        idempotency_key: str,
        request_hash: str,
        operation_id: UUID,
    ) -> StoredIdempotency:
        await barrier.wait(repository._session)
        return await original_acquire(
            repository,
            scope=scope,
            actor_type=actor_type,
            actor_id=actor_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            operation_id=operation_id,
        )

    monkeypatch.setattr(SqlAlchemyIdempotencyRepository, "acquire", synchronized_acquire)
    results = await asyncio.gather(
        env.service.create_ticket(first), env.service.create_ticket(second)
    )

    assert len(barrier.session_ids) == 2
    assert all(result.ok for result in results)
    assert sum(result.replayed for result in results) == 1
    assert results[0].resource_id == results[1].resource_id
    async with env.sessions() as session:
        ticket_count = await session.scalar(
            select(func.count())
            .select_from(RepairTicket)
            .where(RepairTicket.issue_location == first.issue_location)
        )
        history_count = await session.scalar(
            select(func.count())
            .select_from(TicketStatusHistory)
            .where(TicketStatusHistory.ticket_id == results[0].resource_id)
        )
        idempotency_count = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == "create_ticket",
                IdempotencyRecord.idempotency_key == key,
                IdempotencyRecord.execution_status == "SUCCEEDED",
            )
        )
    assert (ticket_count, history_count, idempotency_count) == (1, 1, 1)


@pytest.mark.asyncio
async def test_duplicate_ticket_is_rejected_unless_operator_explicitly_overrides(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    location = f"duplicate-{uuid4()}"
    await _create_ticket(env, location=location)
    duplicate = CreateTicketCommand(
        metadata=_metadata(env),
        resident_id=env.resident_id,
        property_id=env.property_id,
        issue_category=IssueCategory.WATER_LEAK,
        issue_location=location,
        issue_description="same unresolved issue",
        severity=Severity.MEDIUM,
    )
    rejected = await env.service.create_ticket(duplicate)
    resident_override = await env.service.create_ticket(
        replace(duplicate, metadata=_metadata(env), allow_duplicate=True)
    )
    overridden = await env.service.create_ticket(
        replace(
            duplicate,
            metadata=_metadata(env, actor_type=ActorType.OPERATOR, actor_id=env.operator_id),
            allow_duplicate=True,
        )
    )
    assert rejected.code == "disallowed_duplicate"
    assert resident_override.code == "disallowed_duplicate"
    assert overridden.ok


@pytest.mark.asyncio
async def test_booking_rejects_worker_without_required_skill_and_rolls_back(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    created = await env.service.create_ticket(
        CreateTicketCommand(
            metadata=_metadata(env),
            resident_id=env.resident_id,
            property_id=env.property_id,
            issue_category=IssueCategory.ELECTRICAL,
            issue_location=f"panel-{uuid4()}",
            issue_description="outlet sparks",
            severity=Severity.HIGH,
        )
    )
    assert created.resource_id is not None
    booking = await env.service.book_appointment(
        BookAppointmentCommand(
            metadata=_metadata(env),
            ticket_id=created.resource_id,
            worker_id=env.worker_ids[0],
            starts_at=env.slot,
            ends_at=env.slot + timedelta(hours=1),
            expected_ticket_version=1,
        )
    )
    assert booking.code == "worker_not_eligible"
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, created.resource_id)
        count = await session.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(Appointment.ticket_id == created.resource_id)
        )
    assert ticket is not None and (ticket.status, ticket.version) == (TicketStatus.OPEN, 1)
    assert count == 0


@pytest.mark.asyncio
async def test_booking_and_rescheduling_reject_past_start_times(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    past_booking = await env.service.book_appointment(
        BookAppointmentCommand(
            metadata=_metadata(env, occurred_at=env.slot),
            ticket_id=ticket_id,
            worker_id=env.worker_ids[0],
            starts_at=env.slot - timedelta(minutes=30),
            ends_at=env.slot + timedelta(minutes=30),
            expected_ticket_version=1,
        )
    )
    assert past_booking.code == "appointment_time_in_past"

    appointment_id = await _book(env, ticket_id)
    past_reschedule = await env.service.reschedule_appointment(
        RescheduleAppointmentCommand(
            metadata=_metadata(env, occurred_at=env.slot),
            ticket_id=ticket_id,
            appointment_id=appointment_id,
            worker_id=env.worker_ids[0],
            starts_at=env.slot - timedelta(minutes=30),
            ends_at=env.slot + timedelta(minutes=30),
            expected_ticket_version=2,
            expected_appointment_version=1,
        )
    )
    assert past_reschedule.code == "appointment_time_in_past"


@pytest.mark.asyncio
async def test_concurrent_booking_for_one_ticket_has_one_winner(
    application_env: ApplicationEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    barrier = AsyncCompetitionBarrier()
    original_update = SqlAlchemyTicketRepository.update

    async def synchronized_update(
        repository: SqlAlchemyTicketRepository,
        snapshot: TicketSnapshot,
        *,
        expected_version: int,
    ) -> None:
        await barrier.wait(repository._session)
        await original_update(repository, snapshot, expected_version=expected_version)

    monkeypatch.setattr(SqlAlchemyTicketRepository, "update", synchronized_update)
    commands = [
        BookAppointmentCommand(
            metadata=_metadata(env),
            ticket_id=ticket_id,
            worker_id=worker_id,
            starts_at=env.slot + timedelta(hours=index * 2),
            ends_at=env.slot + timedelta(hours=index * 2 + 1),
            expected_ticket_version=1,
        )
        for index, worker_id in enumerate(env.worker_ids)
    ]
    results = await asyncio.gather(*(env.service.book_appointment(command) for command in commands))

    assert len(barrier.session_ids) == 2
    assert sum(result.ok for result in results) == 1
    assert {result.code for result in results if not result.ok} == {"version_conflict"}
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        count = await session.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(
                Appointment.ticket_id == ticket_id,
                Appointment.status == AppointmentStatus.BOOKED,
            )
        )
        history_count = await session.scalar(
            select(func.count())
            .select_from(TicketStatusHistory)
            .where(TicketStatusHistory.ticket_id == ticket_id)
        )
        idempotency_count = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == "book_appointment",
                IdempotencyRecord.idempotency_key.in_(
                    [command.metadata.idempotency_key for command in commands]
                ),
            )
        )
    assert ticket is not None and (ticket.status, ticket.version) == (
        TicketStatus.SCHEDULED,
        2,
    )
    assert count == 1
    assert (history_count, idempotency_count) == (2, 1)


@pytest.mark.asyncio
async def test_worker_overlap_rolls_back_losing_ticket_and_idempotency(
    application_env: ApplicationEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = application_env
    first_ticket, second_ticket = await asyncio.gather(_create_ticket(env), _create_ticket(env))
    barrier = AsyncCompetitionBarrier()
    original_update = SqlAlchemyTicketRepository.update

    async def synchronized_update(
        repository: SqlAlchemyTicketRepository,
        snapshot: TicketSnapshot,
        *,
        expected_version: int,
    ) -> None:
        await barrier.wait(repository._session)
        await original_update(repository, snapshot, expected_version=expected_version)

    monkeypatch.setattr(SqlAlchemyTicketRepository, "update", synchronized_update)
    commands = [
        BookAppointmentCommand(
            metadata=_metadata(env, key=f"overlap-{ticket_id}"),
            ticket_id=ticket_id,
            worker_id=env.worker_ids[0],
            starts_at=env.slot,
            ends_at=env.slot + timedelta(hours=1),
            expected_ticket_version=1,
        )
        for ticket_id in (first_ticket, second_ticket)
    ]
    results = await asyncio.gather(*(env.service.book_appointment(command) for command in commands))

    assert len(barrier.session_ids) == 2
    assert sum(result.ok for result in results) == 1
    assert {result.code for result in results if not result.ok} == {"appointment_time_conflict"}
    loser = second_ticket if results[0].ok else first_ticket
    winner = first_ticket if results[0].ok else second_ticket
    loser_key = f"overlap-{loser}"
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, loser)
        winning_ticket = await session.get(RepairTicket, winner)
        idempotency = await session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == loser_key)
        )
        appointments = await session.scalar(
            select(func.count())
            .select_from(Appointment)
            .where(Appointment.ticket_id.in_([first_ticket, second_ticket]))
        )
    assert ticket is not None
    assert winning_ticket is not None
    assert (ticket.status, ticket.version) == (TicketStatus.OPEN, 1)
    assert (winning_ticket.status, winning_ticket.version) == (TicketStatus.SCHEDULED, 2)
    assert appointments == 1
    assert idempotency is None


@pytest.mark.asyncio
async def test_concurrent_reschedule_creates_only_one_replacement(
    application_env: ApplicationEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    appointment_id = await _book(env, ticket_id)
    barrier = AsyncCompetitionBarrier()
    original_update = SqlAlchemyTicketRepository.update

    async def synchronized_update(
        repository: SqlAlchemyTicketRepository,
        snapshot: TicketSnapshot,
        *,
        expected_version: int,
    ) -> None:
        await barrier.wait(repository._session)
        await original_update(repository, snapshot, expected_version=expected_version)

    monkeypatch.setattr(SqlAlchemyTicketRepository, "update", synchronized_update)
    commands = [
        RescheduleAppointmentCommand(
            metadata=_metadata(env),
            ticket_id=ticket_id,
            appointment_id=appointment_id,
            worker_id=worker_id,
            starts_at=env.slot + timedelta(days=1, hours=index * 2),
            ends_at=env.slot + timedelta(days=1, hours=index * 2 + 1),
            expected_ticket_version=2,
            expected_appointment_version=1,
        )
        for index, worker_id in enumerate(env.worker_ids)
    ]
    results = await asyncio.gather(
        *(env.service.reschedule_appointment(command) for command in commands)
    )

    assert len(barrier.session_ids) == 2
    assert sum(result.ok for result in results) == 1
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        rows = list(
            await session.scalars(select(Appointment).where(Appointment.ticket_id == ticket_id))
        )
        ticket_history_count = await session.scalar(
            select(func.count())
            .select_from(TicketStatusHistory)
            .where(TicketStatusHistory.ticket_id == ticket_id)
        )
        appointment_history_count = await session.scalar(
            select(func.count())
            .select_from(AppointmentStatusHistory)
            .where(AppointmentStatusHistory.appointment_id.in_([row.id for row in rows]))
        )
    assert ticket is not None and (ticket.status, ticket.version) == (
        TicketStatus.SCHEDULED,
        3,
    )
    assert len(rows) == 2
    assert sum(row.status is AppointmentStatus.BOOKED for row in rows) == 1
    assert sum(row.status is AppointmentStatus.SUPERSEDED for row in rows) == 1
    assert sorted(row.version for row in rows) == [1, 2]
    assert (ticket_history_count, appointment_history_count) == (3, 3)


async def _seed_pending_acceptance(env: ApplicationEnvironment) -> UUID:
    ticket_id = await _create_ticket(env)
    appointment_id = uuid4()
    event_id = uuid4()
    async with env.sessions.begin() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        assert ticket is not None
        ticket.status = TicketStatus.PENDING_ACCEPTANCE
        ticket.version = 2
        session.add(
            Appointment(
                id=appointment_id,
                ticket_id=ticket_id,
                worker_id=env.worker_ids[0],
                purpose=AppointmentPurpose.INITIAL_REPAIR,
                status=AppointmentStatus.FULFILLED,
                scheduled_range=Range(env.slot, env.slot + timedelta(hours=1), bounds="[)"),
                version=2,
            )
        )
        await session.flush()
        session.add(
            WorkerEvent(
                id=event_id,
                appointment_id=appointment_id,
                subject_worker_id=env.worker_ids[0],
                sequence_no=1,
                event_type=WorkerEventType.COMPLETED,
                actor_type=ActorType.WORKER,
                actor_id=str(env.worker_ids[0]),
                external_event_key=f"seed-{event_id}",
                request_hash=uuid4().hex + uuid4().hex,
                trace_id=uuid4(),
                occurred_at=env.slot,
                payload={},
            )
        )
    return ticket_id


@pytest.mark.asyncio
async def test_concurrent_resident_rejection_increments_rework_once(
    application_env: ApplicationEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = application_env
    ticket_id = await _seed_pending_acceptance(env)
    barrier = AsyncCompetitionBarrier()
    original_update = SqlAlchemyTicketRepository.update

    async def synchronized_update(
        repository: SqlAlchemyTicketRepository,
        snapshot: TicketSnapshot,
        *,
        expected_version: int,
    ) -> None:
        await barrier.wait(repository._session)
        await original_update(repository, snapshot, expected_version=expected_version)

    monkeypatch.setattr(SqlAlchemyTicketRepository, "update", synchronized_update)
    commands = [
        ReviewRepairCommand(
            metadata=_metadata(env),
            ticket_id=ticket_id,
            expected_ticket_version=2,
            accepted=False,
            rejection_reason=AcceptanceRejectionReason.ISSUE_NOT_RESOLVED,
            explanation="still leaking",
        )
        for _ in range(2)
    ]
    results = await asyncio.gather(*(env.service.review_repair(item) for item in commands))

    assert len(barrier.session_ids) == 2
    assert sum(result.ok for result in results) == 1
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        history_count = await session.scalar(
            select(func.count())
            .select_from(TicketStatusHistory)
            .where(TicketStatusHistory.ticket_id == ticket_id)
        )
        idempotency_count = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == "review_repair",
                IdempotencyRecord.idempotency_key.in_(
                    [command.metadata.idempotency_key for command in commands]
                ),
            )
        )
    assert ticket is not None
    assert (ticket.status, ticket.version, ticket.rework_count) == (
        TicketStatus.REWORK_REQUIRED,
        3,
        1,
    )
    assert (history_count, idempotency_count) == (2, 1)


@pytest.mark.asyncio
async def test_worker_event_replay_sequence_and_atomic_failure(
    application_env: ApplicationEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    appointment_id = await _book(env, ticket_id)
    occurred_at = env.slot - timedelta(hours=1)
    base = RecordWorkerEventCommand(
        metadata=_metadata(
            env,
            actor_type=ActorType.WORKER,
            actor_id=env.worker_ids[0],
            occurred_at=occurred_at,
        ),
        ticket_id=ticket_id,
        appointment_id=appointment_id,
        subject_worker_id=env.worker_ids[0],
        event_type=WorkerEventType.ACCEPTED,
        external_event_key=f"accepted-{uuid4()}",
        expected_ticket_version=2,
        expected_appointment_version=1,
    )
    retry = replace(
        base,
        metadata=_metadata(
            env,
            actor_type=ActorType.WORKER,
            actor_id=env.worker_ids[0],
            occurred_at=occurred_at,
        ),
    )
    barrier = AsyncCompetitionBarrier()
    original_get = SqlAlchemyAppointmentRepository.get

    async def synchronized_get(
        repository: SqlAlchemyAppointmentRepository,
        target_appointment_id: UUID,
        *,
        for_update: bool = False,
    ) -> AppointmentSnapshot | None:
        if for_update:
            await barrier.wait(repository._session)
        return await original_get(repository, target_appointment_id, for_update=for_update)

    monkeypatch.setattr(SqlAlchemyAppointmentRepository, "get", synchronized_get)
    results = await asyncio.gather(
        env.service.record_worker_event(base), env.service.record_worker_event(retry)
    )
    assert len(barrier.session_ids) == 2
    assert all(result.ok for result in results), results
    assert sum(result.replayed for result in results) == 1

    content_conflict = await env.service.record_worker_event(
        replace(
            base,
            metadata=_metadata(env, actor_type=ActorType.WORKER, actor_id=env.worker_ids[0]),
            event_type=WorkerEventType.DEPARTED,
        )
    )
    assert content_conflict.code == "worker_event_payload_conflict"

    invalid = await env.service.record_worker_event(
        replace(
            base,
            metadata=_metadata(env, actor_type=ActorType.WORKER, actor_id=env.worker_ids[0]),
            event_type=WorkerEventType.ARRIVED,
            external_event_key=f"arrived-{uuid4()}",
        )
    )
    assert invalid.code == "missing_predecessor"
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment = await session.get(Appointment, appointment_id)
        event_count = await session.scalar(
            select(func.count())
            .select_from(WorkerEvent)
            .where(WorkerEvent.appointment_id == appointment_id)
        )
    assert ticket is not None and appointment is not None
    assert (ticket.status, ticket.version) == (TicketStatus.SCHEDULED, 2)
    assert (appointment.status, appointment.version) == (AppointmentStatus.BOOKED, 1)
    assert event_count == 1


@pytest.mark.asyncio
async def test_concurrent_distinct_worker_events_revalidate_after_lock(
    application_env: ApplicationEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    appointment_id = await _book(env, ticket_id)
    commands = [
        RecordWorkerEventCommand(
            metadata=_metadata(env, actor_type=ActorType.WORKER, actor_id=env.worker_ids[0]),
            ticket_id=ticket_id,
            appointment_id=appointment_id,
            subject_worker_id=env.worker_ids[0],
            event_type=WorkerEventType.ACCEPTED,
            external_event_key=f"distinct-accepted-{uuid4()}",
            expected_ticket_version=2,
            expected_appointment_version=1,
        )
        for _ in range(2)
    ]
    barrier = AsyncCompetitionBarrier()
    original_get = SqlAlchemyAppointmentRepository.get

    async def synchronized_get(
        repository: SqlAlchemyAppointmentRepository,
        target_appointment_id: UUID,
        *,
        for_update: bool = False,
    ) -> AppointmentSnapshot | None:
        if for_update:
            await barrier.wait(repository._session)
        return await original_get(repository, target_appointment_id, for_update=for_update)

    monkeypatch.setattr(SqlAlchemyAppointmentRepository, "get", synchronized_get)
    results = await asyncio.gather(
        *(env.service.record_worker_event(command) for command in commands)
    )

    assert len(barrier.session_ids) == 2
    assert sum(result.ok for result in results) == 1
    assert {result.code for result in results if not result.ok} == {"accepted_must_be_first"}
    async with env.sessions() as session:
        events = list(
            await session.scalars(
                select(WorkerEvent)
                .where(WorkerEvent.appointment_id == appointment_id)
                .order_by(WorkerEvent.sequence_no)
            )
        )
        appointment = await session.get(Appointment, appointment_id)
        ticket = await session.get(RepairTicket, ticket_id)
        successful_idempotency = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == "record_worker_event",
                IdempotencyRecord.resource_id == events[0].id,
            )
        )
    assert [event.sequence_no for event in events] == [1]
    assert appointment is not None and (appointment.status, appointment.version) == (
        AppointmentStatus.BOOKED,
        1,
    )
    assert ticket is not None and (ticket.status, ticket.version) == (TicketStatus.SCHEDULED, 2)
    assert successful_idempotency == 1


@pytest.mark.asyncio
async def test_permission_failure_writes_nothing(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    key = f"unauthorized-{uuid4()}"
    result = await env.service.book_appointment(
        BookAppointmentCommand(
            metadata=_metadata(env, actor_id=uuid4(), key=key),
            ticket_id=ticket_id,
            worker_id=env.worker_ids[0],
            starts_at=env.slot,
            ends_at=env.slot + timedelta(hours=1),
            expected_ticket_version=1,
        )
    )
    assert result.code == "resident_not_authorized"
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment_count = await session.scalar(
            select(func.count()).select_from(Appointment).where(Appointment.ticket_id == ticket_id)
        )
        idempotency_count = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == key)
        )
    assert ticket is not None and (ticket.status, ticket.version) == (TicketStatus.OPEN, 1)
    assert appointment_count == 0
    assert idempotency_count == 0


@pytest.mark.asyncio
async def test_operator_escalation_and_safe_recovery(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    escalated = await env.service.escalate_ticket(
        EscalateTicketCommand(
            metadata=_metadata(env, actor_type=ActorType.OPERATOR, actor_id=env.operator_id),
            ticket_id=ticket_id,
            expected_ticket_version=1,
            reason_code="MANUAL_REVIEW",
            reason_text="operator review required",
            evidence=("operator-note",),
        )
    )
    recovered = await env.service.recover_ticket(
        RecoverTicketCommand(
            metadata=_metadata(env, actor_type=ActorType.OPERATOR, actor_id=env.operator_id),
            ticket_id=ticket_id,
            expected_ticket_version=2,
            disposition=EscalationDisposition.RESUME,
            conflict_resolved=True,
            reason_text="review complete",
        )
    )

    assert escalated.ok and recovered.ok
    assert recovered.data["status"] == TicketStatus.OPEN.value


@pytest.mark.asyncio
async def test_operator_cannot_substitute_resident_acceptance(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _seed_pending_acceptance(env)
    async with env.sessions.begin() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        assert ticket is not None
        ticket.status = TicketStatus.ESCALATED
        ticket.escalated_from_status = TicketStatus.PENDING_ACCEPTANCE
    result = await env.service.recover_ticket(
        RecoverTicketCommand(
            metadata=_metadata(env, actor_type=ActorType.OPERATOR, actor_id=env.operator_id),
            ticket_id=ticket_id,
            expected_ticket_version=2,
            disposition=EscalationDisposition.APPLY_RESIDENT_ACCEPTANCE,
            conflict_resolved=True,
            resident_acceptance=True,
        )
    )
    assert result.code == "resident_acceptance_requires_resident_command"


@pytest.mark.asyncio
async def test_unauthorized_ticket_creation_rolls_back_everything(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    key = f"unauthorized-create-{uuid4()}"
    location = f"kitchen-{uuid4()}"
    metadata = _metadata(env, actor_id=uuid4(), key=key)
    result = await env.service.create_ticket(
        CreateTicketCommand(
            metadata=metadata,
            resident_id=env.resident_id,
            property_id=env.property_id,
            issue_category=IssueCategory.WATER_LEAK,
            issue_location=location,
            issue_description="unauthorized request",
            severity=Severity.MEDIUM,
        )
    )
    assert result.code == "resident_not_authorized"
    async with env.sessions() as session:
        tickets = await session.scalar(
            select(func.count())
            .select_from(RepairTicket)
            .where(RepairTicket.issue_location == location)
        )
        histories = await session.scalar(
            select(func.count())
            .select_from(TicketStatusHistory)
            .where(TicketStatusHistory.trace_id == metadata.trace_id)
        )
        idempotency = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == key)
        )
    assert (tickets, histories, idempotency) == (0, 0, 0)


@pytest.mark.asyncio
async def test_booking_outside_worker_availability_writes_nothing(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    key = f"outside-availability-{uuid4()}"
    result = await env.service.book_appointment(
        BookAppointmentCommand(
            metadata=_metadata(env, key=key),
            ticket_id=ticket_id,
            worker_id=env.worker_ids[0],
            starts_at=env.slot + timedelta(days=60),
            ends_at=env.slot + timedelta(days=60, hours=1),
            expected_ticket_version=1,
        )
    )
    assert result.code == "worker_not_eligible"
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointments = await session.scalar(
            select(func.count()).select_from(Appointment).where(Appointment.ticket_id == ticket_id)
        )
        idempotency = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == key)
        )
    assert ticket is not None and (ticket.status, ticket.version) == (TicketStatus.OPEN, 1)
    assert (appointments, idempotency) == (0, 0)


@pytest.mark.asyncio
async def test_stale_appointment_version_rolls_back_reschedule(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    appointment_id = await _book(env, ticket_id)
    key = f"stale-reschedule-{uuid4()}"
    result = await env.service.reschedule_appointment(
        RescheduleAppointmentCommand(
            metadata=_metadata(env, key=key),
            ticket_id=ticket_id,
            appointment_id=appointment_id,
            worker_id=env.worker_ids[1],
            starts_at=env.slot + timedelta(days=1),
            ends_at=env.slot + timedelta(days=1, hours=1),
            expected_ticket_version=2,
            expected_appointment_version=99,
        )
    )
    assert result.code == "version_conflict"
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment = await session.get(Appointment, appointment_id)
        appointment_count = await session.scalar(
            select(func.count()).select_from(Appointment).where(Appointment.ticket_id == ticket_id)
        )
        idempotency = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == key)
        )
    assert ticket is not None and appointment is not None
    assert (ticket.status, ticket.version) == (TicketStatus.SCHEDULED, 2)
    assert (appointment.status, appointment.version) == (AppointmentStatus.BOOKED, 1)
    assert (appointment_count, idempotency) == (1, 0)


@pytest.mark.asyncio
async def test_reschedule_constraint_failure_rolls_back_all_writes(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    blocking_ticket = await _create_ticket(env)
    await _book(env, blocking_ticket, worker_id=env.worker_ids[0], starts_at=env.slot)
    target_ticket = await _create_ticket(env)
    old_appointment_id = await _book(
        env,
        target_ticket,
        worker_id=env.worker_ids[1],
        starts_at=env.slot + timedelta(hours=3),
    )
    key = f"failed-reschedule-{uuid4()}"
    result = await env.service.reschedule_appointment(
        RescheduleAppointmentCommand(
            metadata=_metadata(env, key=key),
            ticket_id=target_ticket,
            appointment_id=old_appointment_id,
            worker_id=env.worker_ids[0],
            starts_at=env.slot,
            ends_at=env.slot + timedelta(hours=1),
            expected_ticket_version=2,
            expected_appointment_version=1,
        )
    )
    assert result.code == "appointment_time_conflict"
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, target_ticket)
        old = await session.get(Appointment, old_appointment_id)
        appointments = list(
            await session.scalars(select(Appointment).where(Appointment.ticket_id == target_ticket))
        )
        ticket_histories = await session.scalar(
            select(func.count())
            .select_from(TicketStatusHistory)
            .where(TicketStatusHistory.ticket_id == target_ticket)
        )
        appointment_histories = await session.scalar(
            select(func.count())
            .select_from(AppointmentStatusHistory)
            .where(AppointmentStatusHistory.appointment_id == old_appointment_id)
        )
        idempotency = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == key)
        )
    assert ticket is not None and old is not None
    assert (ticket.status, ticket.version) == (TicketStatus.SCHEDULED, 2)
    assert (old.status, old.version) == (AppointmentStatus.BOOKED, 1)
    assert len(appointments) == 1
    assert (ticket_histories, appointment_histories, idempotency) == (2, 1, 0)


@pytest.mark.asyncio
async def test_initial_worker_cancellation_returns_ticket_to_open(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    appointment_id = await _book(env, ticket_id)
    accepted = RecordWorkerEventCommand(
        metadata=_metadata(env, actor_type=ActorType.WORKER, actor_id=env.worker_ids[0]),
        ticket_id=ticket_id,
        appointment_id=appointment_id,
        subject_worker_id=env.worker_ids[0],
        event_type=WorkerEventType.ACCEPTED,
        external_event_key=f"accepted-{uuid4()}",
        expected_ticket_version=2,
        expected_appointment_version=1,
    )
    assert (await env.service.record_worker_event(accepted)).ok
    cancelled = await env.service.record_worker_event(
        replace(
            accepted,
            metadata=_metadata(env, actor_type=ActorType.WORKER, actor_id=env.worker_ids[0]),
            event_type=WorkerEventType.CANCELLED,
            external_event_key=f"cancelled-{uuid4()}",
            cancellation_reason=CancellationReason.WORKER_CANCELLED,
            reason_text="worker cancelled after accepting",
            evidence=("worker-note",),
        )
    )
    assert cancelled.ok
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment = await session.get(Appointment, appointment_id)
    assert ticket is not None and appointment is not None
    assert (ticket.status, ticket.rework_count, ticket.version) == (TicketStatus.OPEN, 0, 3)
    assert (appointment.status, appointment.version, appointment.outcome_reason_code) == (
        AppointmentStatus.CANCELLED,
        2,
        CancellationReason.WORKER_CANCELLED.value,
    )


@pytest.mark.asyncio
async def test_rework_worker_cancellation_preserves_rework_fact(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    initial_id = await _book(env, ticket_id)
    await _complete_repair(env, ticket_id, initial_id)
    rejected = await env.service.review_repair(
        ReviewRepairCommand(
            metadata=_metadata(env),
            ticket_id=ticket_id,
            expected_ticket_version=4,
            accepted=False,
            rejection_reason=AcceptanceRejectionReason.ISSUE_NOT_RESOLVED,
            explanation="still leaking",
        )
    )
    assert rejected.ok
    rework_id = await _book(
        env,
        ticket_id,
        starts_at=env.slot + timedelta(days=2),
        expected_ticket_version=5,
    )
    accepted = RecordWorkerEventCommand(
        metadata=_metadata(env, actor_type=ActorType.WORKER, actor_id=env.worker_ids[0]),
        ticket_id=ticket_id,
        appointment_id=rework_id,
        subject_worker_id=env.worker_ids[0],
        event_type=WorkerEventType.ACCEPTED,
        external_event_key=f"rework-accepted-{uuid4()}",
        expected_ticket_version=6,
        expected_appointment_version=1,
    )
    assert (await env.service.record_worker_event(accepted)).ok
    cancelled = await env.service.record_worker_event(
        replace(
            accepted,
            metadata=_metadata(env, actor_type=ActorType.WORKER, actor_id=env.worker_ids[0]),
            event_type=WorkerEventType.CANCELLED,
            external_event_key=f"rework-cancelled-{uuid4()}",
            cancellation_reason=CancellationReason.WORKER_CANCELLED,
            reason_text="cannot attend rework",
            evidence=("worker-note",),
        )
    )
    assert cancelled.ok
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment = await session.get(Appointment, rework_id)
    assert ticket is not None and appointment is not None
    assert (ticket.status, ticket.rework_count, ticket.version) == (
        TicketStatus.REWORK_REQUIRED,
        1,
        7,
    )
    assert (appointment.purpose, appointment.status, appointment.version) == (
        AppointmentPurpose.REWORK,
        AppointmentStatus.CANCELLED,
        2,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_reason", "expected_status", "expected_rework"),
    [
        (FailureReason.REPAIR_INCOMPLETE, TicketStatus.REWORK_REQUIRED, 1),
        (FailureReason.SAFETY_RISK, TicketStatus.ESCALATED, 0),
    ],
)
async def test_failed_repair_routes_atomically(
    application_env: ApplicationEnvironment,
    failure_reason: FailureReason,
    expected_status: TicketStatus,
    expected_rework: int,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    appointment_id = await _book(env, ticket_id)
    await _start_repair(env, ticket_id, appointment_id)
    result = await env.service.record_worker_event(
        RecordWorkerEventCommand(
            metadata=_metadata(env, actor_type=ActorType.WORKER, actor_id=env.worker_ids[0]),
            ticket_id=ticket_id,
            appointment_id=appointment_id,
            subject_worker_id=env.worker_ids[0],
            event_type=WorkerEventType.FAILED_TO_COMPLETE,
            external_event_key=f"failed-{uuid4()}",
            expected_ticket_version=3,
            expected_appointment_version=1,
            failure_reason=failure_reason,
            worker_statement="repair could not be completed",
            evidence=("worker-photo",),
        )
    )
    assert result.ok
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        appointment = await session.get(Appointment, appointment_id)
        event_count = await session.scalar(
            select(func.count())
            .select_from(WorkerEvent)
            .where(WorkerEvent.appointment_id == appointment_id)
        )
    assert ticket is not None and appointment is not None
    assert (ticket.status, ticket.version, ticket.rework_count) == (
        expected_status,
        4,
        expected_rework,
    )
    assert (appointment.status, appointment.version) == (AppointmentStatus.FULFILLED, 2)
    assert event_count == 5


@pytest.mark.asyncio
async def test_safe_recovery_failure_leaves_ticket_escalated(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    escalated = await env.service.escalate_ticket(
        EscalateTicketCommand(
            metadata=_metadata(env, actor_type=ActorType.OPERATOR, actor_id=env.operator_id),
            ticket_id=ticket_id,
            expected_ticket_version=1,
            reason_code="MANUAL_REVIEW",
            reason_text="unresolved conflict",
            evidence=("operator-note",),
        )
    )
    assert escalated.ok
    key = f"unsafe-recovery-{uuid4()}"
    failed = await env.service.recover_ticket(
        RecoverTicketCommand(
            metadata=_metadata(
                env, actor_type=ActorType.OPERATOR, actor_id=env.operator_id, key=key
            ),
            ticket_id=ticket_id,
            expected_ticket_version=2,
            disposition=EscalationDisposition.RESUME,
            conflict_resolved=False,
        )
    )
    assert not failed.ok
    async with env.sessions() as session:
        ticket = await session.get(RepairTicket, ticket_id)
        idempotency = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == key)
        )
    assert ticket is not None
    assert (ticket.status, ticket.escalated_from_status, ticket.version) == (
        TicketStatus.ESCALATED,
        TicketStatus.OPEN,
        2,
    )
    assert idempotency == 0


@pytest.mark.asyncio
async def test_stale_pending_is_reported_without_business_write(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    key = f"stale-pending-{uuid4()}"
    command = CreateTicketCommand(
        metadata=_metadata(env, key=key),
        resident_id=env.resident_id,
        property_id=env.property_id,
        issue_category=IssueCategory.WATER_LEAK,
        issue_location=f"pending-{uuid4()}",
        issue_description="request retained as pending",
        severity=Severity.MEDIUM,
    )
    async with env.sessions.begin() as session:
        session.add(
            IdempotencyRecord(
                id=uuid4(),
                scope="create_ticket",
                actor_type=ActorType.RESIDENT,
                actor_id=str(env.resident_id),
                idempotency_key=key,
                request_hash=_request_hash(asdict(command)),
                execution_status=IdempotencyExecutionStatus.PENDING,
            )
        )
    result = await env.service.create_ticket(command)
    assert result.code == "idempotency_request_in_progress"
    async with env.sessions() as session:
        ticket_count = await session.scalar(
            select(func.count())
            .select_from(RepairTicket)
            .where(RepairTicket.issue_location == command.issue_location)
        )
        record = await session.scalar(
            select(IdempotencyRecord).where(IdempotencyRecord.idempotency_key == key)
        )
    assert ticket_count == 0
    assert record is not None and record.execution_status is IdempotencyExecutionStatus.PENDING


@pytest.mark.asyncio
async def test_uow_exit_without_commit_rolls_back(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    user_id = uuid4()
    async with SqlAlchemyUnitOfWork(env.sessions) as uow:
        uow.session.add(
            User(
                id=user_id,
                username=f"rollback-{user_id}",
                password_hash="not-plaintext",
                role="OPERATOR",
            )
        )
        await uow.flush()
    async with env.sessions() as session:
        assert await session.get(User, user_id) is None


@pytest.mark.asyncio
async def test_named_active_appointment_constraint_maps_and_rolls_back(
    application_env: ApplicationEnvironment,
) -> None:
    env = application_env
    ticket_id = await _create_ticket(env)
    await _book(env, ticket_id)
    with pytest.raises(ActiveAppointmentExists) as raised:
        async with SqlAlchemyUnitOfWork(env.sessions) as uow:
            await uow.appointments.add(
                AppointmentDraft(
                    appointment_id=uuid4(),
                    ticket_id=ticket_id,
                    worker_id=env.worker_ids[1],
                    purpose=AppointmentPurpose.INITIAL_REPAIR,
                    starts_at=env.slot + timedelta(hours=3),
                    ends_at=env.slot + timedelta(hours=4),
                )
            )
            await uow.flush()
    assert raised.value.code == "active_appointment_exists"
    assert raised.value.__cause__ is not None
    async with env.sessions() as session:
        appointment_count = await session.scalar(
            select(func.count()).select_from(Appointment).where(Appointment.ticket_id == ticket_id)
        )
    assert appointment_count == 1

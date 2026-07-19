"""PostgreSQL constraint tests for the core repair schema."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest


async def _seed_core(connection: asyncpg.Connection) -> dict[str, UUID]:
    resident_id = uuid4()
    property_id = uuid4()
    worker_id = uuid4()
    await connection.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES ($1, $2, $3, $4)",
        resident_id,
        f"resident-{resident_id}",
        "not-a-plaintext-password",
        "RESIDENT",
    )
    await connection.execute(
        "INSERT INTO properties "
        "(id, community_name, building_no, unit_no, room_no, address_text) "
        "VALUES ($1, 'FixFlow Garden', '1', '1', $2, 'test address')",
        property_id,
        property_id.hex[:8],
    )
    await connection.execute(
        "INSERT INTO workers (id, name, service_area) VALUES ($1, $2, 'FixFlow Garden')",
        worker_id,
        f"worker-{worker_id}",
    )
    return {"resident": resident_id, "property": property_id, "worker": worker_id}


async def _ticket(
    connection: asyncpg.Connection,
    core: dict[str, UUID],
    *,
    status: str = "OPEN",
    escalated_from: str | None = None,
    version: int = 1,
    rework_count: int = 0,
) -> UUID:
    ticket_id = uuid4()
    await connection.execute(
        "INSERT INTO repair_tickets "
        "(id, resident_id, property_id, issue_category, issue_location, "
        "issue_description, severity, status, escalated_from_status, version, rework_count) "
        "VALUES ($1, $2, $3, 'WATER_LEAK', 'kitchen', 'pipe leak', 'MEDIUM', "
        "$4, $5, $6, $7)",
        ticket_id,
        core["resident"],
        core["property"],
        status,
        escalated_from,
        version,
        rework_count,
    )
    return ticket_id


async def _appointment(
    connection: asyncpg.Connection,
    *,
    ticket_id: UUID,
    worker_id: UUID,
    starts_at: datetime,
    ends_at: datetime,
    status: str = "BOOKED",
    appointment_id: UUID | None = None,
    supersedes: UUID | None = None,
) -> UUID:
    appointment_id = appointment_id or uuid4()
    await connection.execute(
        "INSERT INTO appointments "
        "(id, ticket_id, worker_id, purpose, status, scheduled_range, "
        "supersedes_appointment_id) "
        "VALUES ($1, $2, $3, 'INITIAL_REPAIR', $4, tstzrange($5, $6, '[)'), $7)",
        appointment_id,
        ticket_id,
        worker_id,
        status,
        starts_at,
        ends_at,
        supersedes,
    )
    return appointment_id


@pytest.mark.asyncio
async def test_invalid_ticket_status_is_rejected(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    with pytest.raises(asyncpg.CheckViolationError):
        await _ticket(db_connection, core, status="UNKNOWN")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version", "rework_count"),
    [(0, 0), (1, -1)],
)
async def test_invalid_ticket_numeric_bounds_are_rejected(
    db_connection: asyncpg.Connection, version: int, rework_count: int
) -> None:
    core = await _seed_core(db_connection)
    with pytest.raises(asyncpg.CheckViolationError):
        await _ticket(db_connection, core, version=version, rework_count=rework_count)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "escalated_from"),
    [("OPEN", "SCHEDULED"), ("ESCALATED", None)],
)
async def test_escalation_prior_must_match_ticket_status(
    db_connection: asyncpg.Connection, status: str, escalated_from: str | None
) -> None:
    core = await _seed_core(db_connection)
    with pytest.raises(asyncpg.CheckViolationError):
        await _ticket(db_connection, core, status=status, escalated_from=escalated_from)


@pytest.mark.asyncio
async def test_relation_rejects_missing_resident(
    db_connection: asyncpg.Connection,
) -> None:
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db_connection.execute(
            "INSERT INTO resident_property_relations (id, resident_id, property_id) "
            "VALUES ($1, $2, $3)",
            uuid4(),
            uuid4(),
            uuid4(),
        )


@pytest.mark.asyncio
async def test_ticket_rejects_missing_property(
    db_connection: asyncpg.Connection,
) -> None:
    resident_id = uuid4()
    await db_connection.execute(
        "INSERT INTO users (id, username, password_hash, role) VALUES ($1, $2, 'hash', 'RESIDENT')",
        resident_id,
        f"resident-{resident_id}",
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db_connection.execute(
            "INSERT INTO repair_tickets "
            "(id, resident_id, property_id, issue_category, issue_location, "
            "issue_description, severity) VALUES "
            "($1, $2, $3, 'WATER_LEAK', 'kitchen', 'leak', 'LOW')",
            uuid4(),
            resident_id,
            uuid4(),
        )


@pytest.mark.asyncio
async def test_appointment_rejects_missing_ticket(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    now = datetime.now(UTC)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await _appointment(
            db_connection,
            ticket_id=uuid4(),
            worker_id=core["worker"],
            starts_at=now,
            ends_at=now + timedelta(hours=1),
        )


@pytest.mark.asyncio
async def test_worker_event_rejects_missing_appointment(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db_connection.execute(
            "INSERT INTO worker_events "
            "(id, appointment_id, subject_worker_id, sequence_no, event_type, "
            "actor_type, actor_id, external_event_key, request_hash, trace_id, occurred_at) "
            "VALUES ($1, $2, $3, 1, 'ACCEPTED', 'WORKER', $4, $5, $6, $7, $8)",
            uuid4(),
            uuid4(),
            core["worker"],
            str(core["worker"]),
            uuid4().hex,
            "a" * 64,
            uuid4(),
            datetime.now(UTC),
        )


@pytest.mark.asyncio
async def test_history_prevents_accidental_ticket_delete(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    ticket_id = await _ticket(db_connection, core)
    await db_connection.execute(
        "INSERT INTO ticket_status_history "
        "(id, ticket_id, from_status, to_status, action, actor_type, actor_id, "
        "trace_id, version_before, version_after) "
        "VALUES ($1, $2, NULL, 'OPEN', 'CREATE', 'RESIDENT', $3, $4, 0, 1)",
        uuid4(),
        ticket_id,
        str(core["resident"]),
        uuid4(),
    )
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await db_connection.execute("DELETE FROM repair_tickets WHERE id = $1", ticket_id)


@pytest.mark.asyncio
async def test_invalid_appointment_status_is_rejected(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    ticket_id = await _ticket(db_connection, core)
    now = datetime.now(UTC)
    with pytest.raises(asyncpg.CheckViolationError):
        await _appointment(
            db_connection,
            ticket_id=ticket_id,
            worker_id=core["worker"],
            starts_at=now,
            ends_at=now + timedelta(hours=1),
            status="UNKNOWN",
        )


@pytest.mark.asyncio
async def test_same_ticket_cannot_have_two_booked_appointments(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    ticket_id = await _ticket(db_connection, core)
    now = datetime.now(UTC)
    await _appointment(
        db_connection,
        ticket_id=ticket_id,
        worker_id=core["worker"],
        starts_at=now,
        ends_at=now + timedelta(hours=1),
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await _appointment(
            db_connection,
            ticket_id=ticket_id,
            worker_id=core["worker"],
            starts_at=now + timedelta(hours=2),
            ends_at=now + timedelta(hours=3),
        )


@pytest.mark.asyncio
async def test_worker_cannot_have_overlapping_booked_appointments(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    first_ticket = await _ticket(db_connection, core)
    second_ticket = await _ticket(db_connection, core)
    now = datetime.now(UTC)
    await _appointment(
        db_connection,
        ticket_id=first_ticket,
        worker_id=core["worker"],
        starts_at=now,
        ends_at=now + timedelta(hours=2),
    )
    with pytest.raises(asyncpg.ExclusionViolationError):
        await _appointment(
            db_connection,
            ticket_id=second_ticket,
            worker_id=core["worker"],
            starts_at=now + timedelta(hours=1),
            ends_at=now + timedelta(hours=3),
        )


@pytest.mark.asyncio
async def test_different_workers_can_share_the_same_interval(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    other_worker = uuid4()
    await db_connection.execute(
        "INSERT INTO workers (id, name, service_area) VALUES ($1, 'other', 'FixFlow Garden')",
        other_worker,
    )
    now = datetime.now(UTC)
    await _appointment(
        db_connection,
        ticket_id=await _ticket(db_connection, core),
        worker_id=core["worker"],
        starts_at=now,
        ends_at=now + timedelta(hours=1),
    )
    await _appointment(
        db_connection,
        ticket_id=await _ticket(db_connection, core),
        worker_id=other_worker,
        starts_at=now,
        ends_at=now + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_terminal_appointment_does_not_block_new_overlapping_booking(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    now = datetime.now(UTC)
    await _appointment(
        db_connection,
        ticket_id=await _ticket(db_connection, core),
        worker_id=core["worker"],
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        status="FULFILLED",
    )
    await _appointment(
        db_connection,
        ticket_id=await _ticket(db_connection, core),
        worker_id=core["worker"],
        starts_at=now,
        ends_at=now + timedelta(hours=1),
    )


@pytest.mark.asyncio
async def test_empty_appointment_range_is_rejected(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    now = datetime.now(UTC)
    with pytest.raises(asyncpg.CheckViolationError):
        await _appointment(
            db_connection,
            ticket_id=await _ticket(db_connection, core),
            worker_id=core["worker"],
            starts_at=now,
            ends_at=now,
        )


@pytest.mark.asyncio
async def test_appointment_cannot_supersede_itself(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    appointment_id = uuid4()
    now = datetime.now(UTC)
    with pytest.raises(asyncpg.CheckViolationError):
        await _appointment(
            db_connection,
            ticket_id=await _ticket(db_connection, core),
            worker_id=core["worker"],
            starts_at=now,
            ends_at=now + timedelta(hours=1),
            appointment_id=appointment_id,
            supersedes=appointment_id,
        )


@pytest.mark.asyncio
async def test_old_appointment_can_only_be_superseded_once(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    now = datetime.now(UTC)
    old_appointment = await _appointment(
        db_connection,
        ticket_id=await _ticket(db_connection, core),
        worker_id=core["worker"],
        starts_at=now,
        ends_at=now + timedelta(hours=1),
        status="FULFILLED",
    )
    await _appointment(
        db_connection,
        ticket_id=await _ticket(db_connection, core),
        worker_id=core["worker"],
        starts_at=now + timedelta(hours=2),
        ends_at=now + timedelta(hours=3),
        supersedes=old_appointment,
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await _appointment(
            db_connection,
            ticket_id=await _ticket(db_connection, core),
            worker_id=core["worker"],
            starts_at=now + timedelta(hours=4),
            ends_at=now + timedelta(hours=5),
            supersedes=old_appointment,
        )


@pytest.mark.asyncio
async def test_invalid_worker_event_type_is_rejected(
    db_connection: asyncpg.Connection,
) -> None:
    core = await _seed_core(db_connection)
    now = datetime.now(UTC)
    ticket_id = await _ticket(db_connection, core)
    appointment_id = await _appointment(
        db_connection,
        ticket_id=ticket_id,
        worker_id=core["worker"],
        starts_at=now,
        ends_at=now + timedelta(hours=1),
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await db_connection.execute(
            "INSERT INTO worker_events "
            "(id, appointment_id, subject_worker_id, sequence_no, event_type, "
            "actor_type, actor_id, external_event_key, request_hash, trace_id, occurred_at) "
            "VALUES ($1, $2, $3, 1, 'SKIPPED', 'WORKER', $4, $5, $6, $7, $8)",
            uuid4(),
            appointment_id,
            core["worker"],
            str(core["worker"]),
            uuid4().hex,
            "b" * 64,
            uuid4(),
            now,
        )

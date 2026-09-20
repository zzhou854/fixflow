"""Real PostgreSQL evidence for slot eligibility, workload, and stable ordering."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.domain.enums import (
    ActorType,
    AppointmentPurpose,
    AppointmentStatus,
    IssueCategory,
    Severity,
)
from app.infrastructure.database.models import (
    Appointment,
    WorkerAvailability,
)
from sqlalchemy import select
from sqlalchemy.dialects.postgresql.ranges import Range

from mcp_server.schemas.appointments import (
    BookAppointmentRequest,
    ListAvailableSlotsRequest,
)
from mcp_server.schemas.common import ResultCode
from mcp_server.schemas.tickets import CreateRepairTicketRequest


def _read(env: Any) -> dict[str, object]:
    return {
        "actor_type": ActorType.RESIDENT,
        "actor_id": env.resident_id,
        "trace_id": uuid4(),
    }


async def _create(env: Any) -> object:
    result = await env.adapter.create_repair_ticket(
        CreateRepairTicketRequest(
            **_read(env),
            idempotency_key=uuid4().hex,
            resident_id=env.resident_id,
            property_id=env.property_id,
            issue_category=IssueCategory.WATER_LEAK,
            issue_location=f"slot-load-{uuid4()}",
            issue_description="load test",
            severity=Severity.MEDIUM,
        )
    )
    assert result.data is not None
    return result.data.resource_id


async def _list(env: Any, *, maximum: int = 100) -> Any:
    return await env.adapter.list_available_slots(
        ListAvailableSlotsRequest(
            **_read(env),
            property_id=env.property_id,
            issue_category=IssueCategory.WATER_LEAK,
            search_window_start=env.slot,
            search_window_end=env.slot + timedelta(hours=2),
            requested_duration_minutes=60,
            max_results=maximum,
        )
    )


@pytest.mark.asyncio
async def test_skill_active_area_availability_and_maximum_filters(mcp_env: Any) -> None:
    env = mcp_env
    async with env.sessions.begin() as session:
        availability = await session.scalar(
            select(WorkerAvailability).where(WorkerAvailability.worker_id == env.worker_ids[1])
        )
        assert availability is not None
        availability.available_range = Range(
            env.slot, env.slot + timedelta(minutes=45), bounds="[)"
        )
    response = await _list(env, maximum=2)
    assert response.result_code is ResultCode.FOUND
    assert response.data is not None
    assert len(response.data.slots) == 2
    assert {row.worker_id for row in response.data.slots} == {env.worker_ids[0]}
    excluded = {
        env.worker_ids[1],
        env.wrong_skill_worker_id,
        env.inactive_worker_id,
        env.wrong_area_worker_id,
    }
    assert not ({row.worker_id for row in response.data.slots} & excluded)


@pytest.mark.asyncio
async def test_terminal_history_does_not_block_but_booked_interval_does(mcp_env: Any) -> None:
    env = mcp_env
    terminal_ticket = await _create(env)
    terminal_id = uuid4()
    blocking_ticket = await _create(env)
    async with env.sessions.begin() as session:
        session.add(
            Appointment(
                id=terminal_id,
                ticket_id=terminal_ticket,
                worker_id=env.worker_ids[0],
                purpose=AppointmentPurpose.INITIAL_REPAIR,
                status=AppointmentStatus.FULFILLED,
                scheduled_range=Range(env.slot, env.slot + timedelta(hours=1), bounds="[)"),
                version=2,
            )
        )
    booked = await env.adapter.book_appointment(
        BookAppointmentRequest(
            **_read(env),
            idempotency_key=uuid4().hex,
            ticket_id=blocking_ticket,
            worker_id=env.worker_ids[1],
            scheduled_start=env.slot,
            scheduled_end=env.slot + timedelta(hours=1),
            expected_version=1,
        )
    )
    assert booked.result_code is ResultCode.CREATED
    response = await _list(env)
    assert response.data is not None
    first_start = [row for row in response.data.slots if row.scheduled_start == env.slot]
    assert [row.worker_id for row in first_start] == [env.worker_ids[0]]


@pytest.mark.asyncio
async def test_workload_then_worker_id_order_is_stable(mcp_env: Any) -> None:
    env = mcp_env
    load_ticket = await _create(env)
    booked = await env.adapter.book_appointment(
        BookAppointmentRequest(
            **_read(env),
            idempotency_key=uuid4().hex,
            ticket_id=load_ticket,
            worker_id=env.worker_ids[0],
            scheduled_start=env.slot + timedelta(days=1),
            scheduled_end=env.slot + timedelta(days=1, hours=1),
            expected_version=1,
        )
    )
    assert booked.result_code is ResultCode.CREATED
    first = await _list(env)
    second = await _list(env)
    assert first.data is not None and second.data is not None
    assert first.data.slots == second.data.slots
    at_start = [row for row in first.data.slots if row.scheduled_start == env.slot]
    assert [row.open_ticket_count for row in at_start] == [0, 1]
    assert [row.worker_id for row in at_start] == [env.worker_ids[1], env.worker_ids[0]]


@pytest.mark.asyncio
async def test_worker_id_breaks_equal_workload_ties(mcp_env: Any) -> None:
    env = mcp_env
    response = await _list(env)
    assert response.data is not None
    at_start = [row.worker_id for row in response.data.slots if row.scheduled_start == env.slot]
    assert at_start == sorted(env.worker_ids)

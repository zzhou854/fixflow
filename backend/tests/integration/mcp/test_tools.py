"""Eight MCP adapters exercised through real Application services and PostgreSQL."""

from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from app.domain.enums import ActorType, AppointmentStatus, IssueCategory, Severity, TicketStatus
from app.infrastructure.database.models import Appointment, IdempotencyRecord, RepairTicket
from sqlalchemy import func, select

from mcp_server.schemas.appointments import (
    BookAppointmentRequest,
    ListAvailableSlotsRequest,
    RescheduleAppointmentRequest,
)
from mcp_server.schemas.common import ResultCode
from mcp_server.schemas.properties import GetResidentPropertyRequest
from mcp_server.schemas.tickets import (
    CreateRepairTicketRequest,
    EscalateToOperatorRequest,
    EscalationReasonCode,
    FindOpenRepairTicketsRequest,
    GetTicketSnapshotRequest,
)


def _read(env: Any, *, actor_id: object | None = None) -> dict[str, object]:
    return {
        "actor_type": ActorType.RESIDENT,
        "actor_id": actor_id or env.resident_id,
        "trace_id": uuid4(),
    }


def _mutation(
    env: Any, *, key: str | None = None, actor_id: object | None = None
) -> dict[str, object]:
    return {
        **_read(env, actor_id=actor_id),
        "idempotency_key": key or uuid4().hex,
    }


def _create_request(
    env: Any, *, key: str | None = None, location: str | None = None
) -> CreateRepairTicketRequest:
    return CreateRepairTicketRequest(
        **_mutation(env, key=key),
        resident_id=env.resident_id,
        property_id=env.property_id,
        issue_category=IssueCategory.WATER_LEAK,
        issue_location=location or f"kitchen-{uuid4()}",
        issue_description="pipe is leaking",
        severity=Severity.MEDIUM,
    )


@pytest.mark.asyncio
async def test_all_eight_tools_follow_the_real_application_and_database(mcp_env: Any) -> None:
    env = mcp_env
    property_response = await env.adapter.get_resident_property(
        GetResidentPropertyRequest(
            **_read(env), resident_id=env.resident_id, property_id=env.property_id
        )
    )
    assert property_response.result_code is ResultCode.FOUND
    assert property_response.data is not None
    assert property_response.data.community_name == env.community_name

    create_request = _create_request(env, key=f"create-{uuid4()}")
    created = await env.adapter.create_repair_ticket(create_request)
    replayed = await env.adapter.create_repair_ticket(create_request)
    assert created.result_code is ResultCode.CREATED
    assert replayed.result_code is ResultCode.CREATED
    assert replayed.data is not None and replayed.data.replayed
    assert created.data is not None
    ticket_id = created.data.resource_id

    candidates = await env.adapter.find_open_repair_tickets(
        FindOpenRepairTicketsRequest(
            **_read(env),
            resident_id=env.resident_id,
            property_id=env.property_id,
            issue_category=IssueCategory.WATER_LEAK,
            normalized_issue_location=create_request.issue_location,
        )
    )
    assert candidates.result_code is ResultCode.FOUND
    assert candidates.data is not None
    assert [row.ticket_id for row in candidates.data.tickets] == [ticket_id]
    assert candidates.data.match_type == "EXACT_STRUCTURED_CANDIDATE"

    initial_snapshot = await env.adapter.get_ticket_snapshot(
        GetTicketSnapshotRequest(**_read(env), ticket_id=ticket_id)
    )
    assert initial_snapshot.data is not None
    assert initial_snapshot.data.ticket_status is TicketStatus.OPEN

    slots = await env.adapter.list_available_slots(
        ListAvailableSlotsRequest(
            **_read(env),
            property_id=env.property_id,
            issue_category=IssueCategory.WATER_LEAK,
            search_window_start=env.slot + timedelta(minutes=10),
            search_window_end=env.slot + timedelta(hours=4),
            requested_duration_minutes=60,
            max_results=10,
        )
    )
    assert slots.result_code is ResultCode.FOUND
    assert slots.data is not None and slots.data.slots
    first_slot = slots.data.slots[0]
    assert first_slot.scheduled_start == env.slot + timedelta(minutes=30)
    assert first_slot.service_area_matched
    assert first_slot.worker_id in env.worker_ids

    booked = await env.adapter.book_appointment(
        BookAppointmentRequest(
            **_mutation(env),
            ticket_id=ticket_id,
            worker_id=first_slot.worker_id,
            scheduled_start=first_slot.scheduled_start,
            scheduled_end=first_slot.scheduled_end,
            expected_version=1,
        )
    )
    assert booked.result_code is ResultCode.CREATED
    assert booked.data is not None
    appointment_id = booked.data.resource_id

    replacement_worker = next(item for item in env.worker_ids if item != first_slot.worker_id)
    rescheduled = await env.adapter.reschedule_appointment(
        RescheduleAppointmentRequest(
            **_mutation(env),
            ticket_id=ticket_id,
            appointment_id=appointment_id,
            worker_id=replacement_worker,
            scheduled_start=env.slot + timedelta(hours=3),
            scheduled_end=env.slot + timedelta(hours=4),
            expected_version=2,
            expected_appointment_version=1,
        )
    )
    assert rescheduled.result_code is ResultCode.UPDATED

    escalated = await env.adapter.escalate_to_operator(
        EscalateToOperatorRequest(
            actor_type=ActorType.OPERATOR,
            actor_id=env.operator_id,
            trace_id=uuid4(),
            idempotency_key=uuid4().hex,
            ticket_id=ticket_id,
            expected_version=3,
            reason_code=EscalationReasonCode.MANUAL_REVIEW,
            reason_text="operator review required",
            evidence=("operator-note",),
        )
    )
    assert escalated.result_code is ResultCode.UPDATED

    final_snapshot = await env.adapter.get_ticket_snapshot(
        GetTicketSnapshotRequest(
            actor_type=ActorType.OPERATOR,
            actor_id=env.operator_id,
            trace_id=uuid4(),
            ticket_id=ticket_id,
        )
    )
    assert final_snapshot.data is not None
    assert (final_snapshot.data.ticket_status, final_snapshot.data.ticket_version) == (
        TicketStatus.ESCALATED,
        4,
    )
    async with env.sessions() as session:
        appointments = list(
            await session.scalars(select(Appointment).where(Appointment.ticket_id == ticket_id))
        )
        idempotency_count = await session.scalar(
            select(func.count())
            .select_from(IdempotencyRecord)
            .where(IdempotencyRecord.idempotency_key == create_request.idempotency_key)
        )
    assert len(appointments) == 2
    assert {row.status for row in appointments} == {
        AppointmentStatus.SUPERSEDED,
        AppointmentStatus.BOOKED,
    }
    assert idempotency_count == 1


@pytest.mark.asyncio
async def test_permissions_versions_and_wrong_area_fail_without_partial_writes(
    mcp_env: Any,
) -> None:
    env = mcp_env
    denied_property = await env.adapter.get_resident_property(
        GetResidentPropertyRequest(
            **_read(env, actor_id=env.other_resident_id),
            resident_id=env.resident_id,
            property_id=env.property_id,
        )
    )
    assert denied_property.result_code is ResultCode.PERMISSION_DENIED

    denied_location = f"denied-{uuid4()}"
    denied_create = _create_request(env, location=denied_location)
    denied_create = denied_create.model_copy(
        update={"actor_id": env.other_resident_id, "trace_id": uuid4()}
    )
    denied = await env.adapter.create_repair_ticket(denied_create)
    assert denied.result_code is ResultCode.PERMISSION_DENIED

    created = await env.adapter.create_repair_ticket(_create_request(env))
    assert created.data is not None
    ticket_id = created.data.resource_id
    wrong_area = await env.adapter.book_appointment(
        BookAppointmentRequest(
            **_mutation(env),
            ticket_id=ticket_id,
            worker_id=env.wrong_area_worker_id,
            scheduled_start=env.slot,
            scheduled_end=env.slot + timedelta(hours=1),
            expected_version=1,
        )
    )
    stale = await env.adapter.book_appointment(
        BookAppointmentRequest(
            **_mutation(env),
            ticket_id=ticket_id,
            worker_id=env.worker_ids[0],
            scheduled_start=env.slot,
            scheduled_end=env.slot + timedelta(hours=1),
            expected_version=99,
        )
    )
    assert wrong_area.result_code is ResultCode.VALIDATION_ERROR
    assert stale.result_code is ResultCode.VERSION_CONFLICT
    async with env.sessions() as session:
        denied_count = await session.scalar(
            select(func.count())
            .select_from(RepairTicket)
            .where(RepairTicket.issue_location == denied_location)
        )
        appointment_count = await session.scalar(
            select(func.count()).select_from(Appointment).where(Appointment.ticket_id == ticket_id)
        )
    assert (denied_count, appointment_count) == (0, 0)


@pytest.mark.asyncio
async def test_candidate_can_lose_to_later_booking_and_returns_time_conflict(mcp_env: Any) -> None:
    env = mcp_env
    first = await env.adapter.create_repair_ticket(_create_request(env))
    second = await env.adapter.create_repair_ticket(_create_request(env))
    assert first.data is not None and second.data is not None
    slots = await env.adapter.list_available_slots(
        ListAvailableSlotsRequest(
            **_read(env),
            property_id=env.property_id,
            issue_category=IssueCategory.WATER_LEAK,
            search_window_start=env.slot,
            search_window_end=env.slot + timedelta(hours=2),
            requested_duration_minutes=60,
            max_results=1,
        )
    )
    assert slots.data is not None and slots.data.slots
    candidate = slots.data.slots[0]
    winner = await env.adapter.book_appointment(
        BookAppointmentRequest(
            **_mutation(env),
            ticket_id=second.data.resource_id,
            worker_id=candidate.worker_id,
            scheduled_start=candidate.scheduled_start,
            scheduled_end=candidate.scheduled_end,
            expected_version=1,
        )
    )
    loser = await env.adapter.book_appointment(
        BookAppointmentRequest(
            **_mutation(env),
            ticket_id=first.data.resource_id,
            worker_id=candidate.worker_id,
            scheduled_start=candidate.scheduled_start,
            scheduled_end=candidate.scheduled_end,
            expected_version=1,
        )
    )
    assert winner.result_code is ResultCode.CREATED
    assert loser.result_code is ResultCode.TIME_CONFLICT
    async with env.sessions() as session:
        losing_ticket = await session.get(RepairTicket, first.data.resource_id)
    assert losing_ticket is not None
    assert (losing_ticket.status, losing_ticket.version) == (TicketStatus.OPEN, 1)

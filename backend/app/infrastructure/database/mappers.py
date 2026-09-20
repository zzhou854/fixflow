"""Explicit mappings between persistence rows and immutable domain snapshots."""

from app.domain.models import AppointmentSnapshot, TicketSnapshot, WorkerEventSnapshot
from app.infrastructure.database.models.appointment import Appointment
from app.infrastructure.database.models.event import WorkerEvent
from app.infrastructure.database.models.ticket import RepairTicket


def ticket_to_snapshot(row: RepairTicket) -> TicketSnapshot:
    return TicketSnapshot(
        ticket_id=row.id,
        resident_id=row.resident_id,
        property_id=row.property_id,
        issue_category=row.issue_category,
        issue_location=row.issue_location,
        issue_description=row.issue_description,
        severity=row.severity,
        status=row.status,
        escalated_from_status=row.escalated_from_status,
        rework_count=row.rework_count,
        version=row.version,
        cancelled_at=row.cancelled_at,
        closed_at=row.closed_at,
    )


def appointment_to_snapshot(row: Appointment) -> AppointmentSnapshot:
    starts_at = row.scheduled_range.lower
    ends_at = row.scheduled_range.upper
    if starts_at is None or ends_at is None:
        raise ValueError("persisted appointment range must be bounded")
    return AppointmentSnapshot(
        appointment_id=row.id,
        ticket_id=row.ticket_id,
        worker_id=row.worker_id,
        purpose=row.purpose,
        starts_at=starts_at,
        ends_at=ends_at,
        status=row.status,
        version=row.version,
        supersedes_appointment_id=row.supersedes_appointment_id,
    )


def worker_event_to_snapshot(row: WorkerEvent) -> WorkerEventSnapshot:
    return WorkerEventSnapshot(
        event_id=row.id,
        appointment_id=row.appointment_id,
        subject_worker_id=row.subject_worker_id,
        sequence_no=row.sequence_no,
        event_type=row.event_type,
        external_event_key=row.external_event_key,
        request_hash=row.request_hash,
    )

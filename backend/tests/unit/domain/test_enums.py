"""Frozen enum regression tests."""

from app.domain.enums import (
    ESCALATABLE_TICKET_STATUSES,
    TERMINAL_APPOINTMENT_STATUSES,
    ActorType,
    AppointmentPurpose,
    AppointmentStatus,
    TicketStatus,
    WorkerEventType,
    WorkflowStage,
)


def test_ticket_status_values_are_frozen() -> None:
    assert [item.value for item in TicketStatus] == [
        "OPEN",
        "SCHEDULED",
        "IN_PROGRESS",
        "PENDING_ACCEPTANCE",
        "REWORK_REQUIRED",
        "ESCALATED",
        "CANCELLED",
        "CLOSED",
    ]


def test_appointment_status_values_and_terminal_set_are_frozen() -> None:
    assert [item.value for item in AppointmentStatus] == [
        "BOOKED",
        "FULFILLED",
        "SUPERSEDED",
        "CANCELLED",
        "NO_SHOW",
    ]
    assert TERMINAL_APPOINTMENT_STATUSES == frozenset(
        {
            AppointmentStatus.FULFILLED,
            AppointmentStatus.SUPERSEDED,
            AppointmentStatus.CANCELLED,
            AppointmentStatus.NO_SHOW,
        }
    )


def test_appointment_purpose_is_typed_for_initial_and_rework_visits() -> None:
    assert [item.value for item in AppointmentPurpose] == ["INITIAL_REPAIR", "REWORK"]


def test_worker_event_values_are_frozen() -> None:
    assert [item.value for item in WorkerEventType] == [
        "ACCEPTED",
        "REJECTED",
        "DEPARTED",
        "ARRIVED",
        "STARTED",
        "COMPLETED",
        "FAILED_TO_COMPLETE",
        "CANCELLED",
        "NO_SHOW",
    ]


def test_workflow_stage_includes_every_approved_value() -> None:
    assert {item.value for item in WorkflowStage} == {
        "INTAKE",
        "NEED_PROPERTY",
        "NEED_INFO",
        "EMERGENCY_REVIEW",
        "POLICY_CHECK",
        "DUPLICATE_CHECK",
        "EXISTING_TICKET",
        "CREATING_TICKET",
        "UNKNOWN_COMMIT",
        "FINDING_SLOTS",
        "AWAITING_SLOT_CONFIRMATION",
        "BOOKING",
        "MONITORING_APPOINTMENT",
        "RESCHEDULING",
        "STATUS_CONFLICT",
        "AWAITING_ACCEPTANCE",
        "PLANNING_REWORK",
        "HUMAN_REVIEW",
        "DONE",
    }


def test_escalatable_ticket_states_and_actors_are_explicit() -> None:
    assert ESCALATABLE_TICKET_STATUSES == frozenset(
        {
            TicketStatus.OPEN,
            TicketStatus.SCHEDULED,
            TicketStatus.IN_PROGRESS,
            TicketStatus.PENDING_ACCEPTANCE,
            TicketStatus.REWORK_REQUIRED,
        }
    )
    assert {item.value for item in ActorType} == {"RESIDENT", "OPERATOR", "WORKER", "SYSTEM"}

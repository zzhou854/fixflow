"""Appointment lifecycle and rescheduling tests."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.domain.appointments import (
    AppointmentTransitionRequest,
    plan_appointment_creation,
    plan_reschedule,
    transition_appointment,
)
from app.domain.enums import (
    ActorType,
    AppointmentAction,
    AppointmentPurpose,
    AppointmentStatus,
    CancellationReason,
    DomainFact,
    NoShowReason,
    TicketStatus,
)
from app.domain.errors import (
    InvalidAppointmentTransition,
    PermissionDenied,
    VersionConflict,
)
from app.domain.models import AppointmentDraft, AppointmentSnapshot, OutcomeMetadata

NOW = datetime(2026, 7, 17, 9, tzinfo=UTC)


def _outcome(
    reason: CancellationReason | NoShowReason,
    actor: ActorType,
) -> OutcomeMetadata:
    return OutcomeMetadata(actor, UUID(int=9), reason, "typed explanation", ("e1",), NOW)


@pytest.mark.parametrize(
    ("action", "actor", "expected", "outcome"),
    [
        (AppointmentAction.SUPERSEDE, ActorType.RESIDENT, AppointmentStatus.SUPERSEDED, None),
        (
            AppointmentAction.CANCEL,
            ActorType.RESIDENT,
            AppointmentStatus.CANCELLED,
            _outcome(CancellationReason.RESIDENT_CANCELLED, ActorType.RESIDENT),
        ),
        (AppointmentAction.FULFILL, ActorType.WORKER, AppointmentStatus.FULFILLED, None),
        (
            AppointmentAction.MARK_NO_SHOW,
            ActorType.OPERATOR,
            AppointmentStatus.NO_SHOW,
            _outcome(NoShowReason.WORKER_NO_SHOW, ActorType.WORKER),
        ),
    ],
)
def test_all_booked_transition_rows(
    action: AppointmentAction,
    actor: ActorType,
    expected: AppointmentStatus,
    outcome: OutcomeMetadata | None,
) -> None:
    result = transition_appointment(
        AppointmentTransitionRequest(
            current_status=AppointmentStatus.BOOKED,
            action=action,
            actor_type=actor,
            expected_version=4,
            actual_version=4,
            outcome=outcome,
        )
    )
    assert result.next_status is expected
    assert result.next_version == 5
    assert result.facts == (DomainFact.APPOINTMENT_TRANSITIONED,)


@pytest.mark.parametrize(
    "terminal",
    [
        AppointmentStatus.FULFILLED,
        AppointmentStatus.SUPERSEDED,
        AppointmentStatus.CANCELLED,
        AppointmentStatus.NO_SHOW,
    ],
)
@pytest.mark.parametrize("action", list(AppointmentAction))
def test_terminal_appointments_have_no_escape_path(
    terminal: AppointmentStatus, action: AppointmentAction
) -> None:
    with pytest.raises(InvalidAppointmentTransition):
        transition_appointment(
            AppointmentTransitionRequest(
                current_status=terminal,
                action=action,
                actor_type=ActorType.OPERATOR,
                expected_version=1,
                actual_version=1,
            )
        )


def test_cancellation_requires_typed_complete_outcome() -> None:
    with pytest.raises(InvalidAppointmentTransition):
        transition_appointment(
            AppointmentTransitionRequest(
                current_status=AppointmentStatus.BOOKED,
                action=AppointmentAction.CANCEL,
                actor_type=ActorType.RESIDENT,
                expected_version=1,
                actual_version=1,
            )
        )


@pytest.mark.parametrize(
    ("actor", "reason"),
    [
        (ActorType.RESIDENT, CancellationReason.RESIDENT_CANCELLED),
        (ActorType.WORKER, CancellationReason.WORKER_CANCELLED),
        (ActorType.OPERATOR, CancellationReason.OPERATOR_CANCELLED),
        (ActorType.SYSTEM, CancellationReason.SYSTEM_CANCELLED),
    ],
)
def test_all_appointment_cancellation_actor_types(
    actor: ActorType, reason: CancellationReason
) -> None:
    result = transition_appointment(
        AppointmentTransitionRequest(
            current_status=AppointmentStatus.BOOKED,
            action=AppointmentAction.CANCEL,
            actor_type=actor,
            expected_version=1,
            actual_version=1,
            outcome=_outcome(reason, actor),
        )
    )
    assert result.next_status is AppointmentStatus.CANCELLED


def test_outcome_actor_must_match_reason_subject() -> None:
    with pytest.raises(InvalidAppointmentTransition):
        transition_appointment(
            AppointmentTransitionRequest(
                current_status=AppointmentStatus.BOOKED,
                action=AppointmentAction.MARK_NO_SHOW,
                actor_type=ActorType.OPERATOR,
                expected_version=1,
                actual_version=1,
                outcome=_outcome(NoShowReason.RESIDENT_NO_SHOW, ActorType.WORKER),
            )
        )


def test_unauthorized_transition_actor() -> None:
    with pytest.raises(PermissionDenied):
        transition_appointment(
            AppointmentTransitionRequest(
                current_status=AppointmentStatus.BOOKED,
                action=AppointmentAction.SUPERSEDE,
                actor_type=ActorType.WORKER,
                expected_version=1,
                actual_version=1,
            )
        )


@pytest.mark.parametrize(
    ("action", "allowed", "denied", "outcome"),
    [
        (AppointmentAction.SUPERSEDE, ActorType.OPERATOR, ActorType.WORKER, None),
        (AppointmentAction.FULFILL, ActorType.RESIDENT, ActorType.SYSTEM, None),
        (
            AppointmentAction.MARK_NO_SHOW,
            ActorType.SYSTEM,
            ActorType.WORKER,
            _outcome(NoShowReason.RESIDENT_NO_SHOW, ActorType.RESIDENT),
        ),
    ],
)
def test_appointment_role_sets_have_positive_and_negative_examples(
    action: AppointmentAction,
    allowed: ActorType,
    denied: ActorType,
    outcome: OutcomeMetadata | None,
) -> None:
    def request(actor: ActorType) -> AppointmentTransitionRequest:
        return AppointmentTransitionRequest(
            current_status=AppointmentStatus.BOOKED,
            action=action,
            actor_type=actor,
            expected_version=1,
            actual_version=1,
            outcome=outcome,
        )

    transition_appointment(request(allowed))
    with pytest.raises(PermissionDenied):
        transition_appointment(request(denied))


def test_appointment_version_conflict() -> None:
    with pytest.raises(VersionConflict):
        transition_appointment(
            AppointmentTransitionRequest(
                current_status=AppointmentStatus.BOOKED,
                action=AppointmentAction.FULFILL,
                actor_type=ActorType.WORKER,
                expected_version=1,
                actual_version=2,
            )
        )


def _booked() -> AppointmentSnapshot:
    return AppointmentSnapshot(
        appointment_id=UUID(int=1),
        ticket_id=UUID(int=2),
        worker_id=UUID(int=3),
        purpose=AppointmentPurpose.INITIAL_REPAIR,
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=1),
        status=AppointmentStatus.BOOKED,
        version=2,
    )


def _replacement(old: AppointmentSnapshot) -> AppointmentDraft:
    return AppointmentDraft(
        appointment_id=UUID(int=4),
        ticket_id=old.ticket_id,
        worker_id=UUID(int=5),
        purpose=old.purpose,
        starts_at=NOW + timedelta(days=1),
        ends_at=NOW + timedelta(days=1, hours=1),
        supersedes_appointment_id=old.appointment_id,
    )


def test_formal_appointment_creation_is_booked_and_versions_ticket() -> None:
    draft = replace(_replacement(_booked()), supersedes_appointment_id=None)
    plan = plan_appointment_creation(
        draft,
        current_ticket_status=TicketStatus.OPEN,
        actor_type=ActorType.RESIDENT,
        expected_ticket_version=3,
        actual_ticket_version=3,
    )
    assert plan.new_appointment.status is AppointmentStatus.BOOKED
    assert plan.next_ticket_status is TicketStatus.SCHEDULED
    assert plan.next_ticket_version == 4


def test_rework_appointment_creation_requires_rework_purpose() -> None:
    initial_draft = replace(_replacement(_booked()), supersedes_appointment_id=None)
    with pytest.raises(InvalidAppointmentTransition):
        plan_appointment_creation(
            initial_draft,
            current_ticket_status=TicketStatus.REWORK_REQUIRED,
            actor_type=ActorType.RESIDENT,
            expected_ticket_version=3,
            actual_ticket_version=3,
        )
    rework_draft = replace(initial_draft, purpose=AppointmentPurpose.REWORK)
    plan = plan_appointment_creation(
        rework_draft,
        current_ticket_status=TicketStatus.REWORK_REQUIRED,
        actor_type=ActorType.RESIDENT,
        expected_ticket_version=3,
        actual_ticket_version=3,
    )
    assert plan.new_appointment.purpose is AppointmentPurpose.REWORK


def test_reschedule_is_one_indivisible_plan_and_does_not_mutate_old() -> None:
    old = _booked()
    candidate = _replacement(old)
    assert old.status is AppointmentStatus.BOOKED

    plan = plan_reschedule(old, candidate, actor_type=ActorType.RESIDENT, expected_version=2)

    assert old.status is AppointmentStatus.BOOKED
    assert plan.old_transition.next_status is AppointmentStatus.SUPERSEDED
    assert plan.old_transition.next_version == 3
    assert plan.new_appointment.status is AppointmentStatus.BOOKED
    assert plan.new_appointment.supersedes_appointment_id == old.appointment_id
    assert plan.facts == (DomainFact.APPOINTMENT_RESCHEDULED,)


@pytest.mark.parametrize(
    "replacement",
    [
        replace(_replacement(_booked()), appointment_id=UUID(int=1)),
        replace(_replacement(_booked()), ticket_id=UUID(int=99)),
        replace(_replacement(_booked()), supersedes_appointment_id=None),
        replace(_replacement(_booked()), purpose=AppointmentPurpose.REWORK),
    ],
)
def test_invalid_reschedule_cannot_express_partial_success(
    replacement: AppointmentDraft,
) -> None:
    with pytest.raises(InvalidAppointmentTransition):
        plan_reschedule(_booked(), replacement, actor_type=ActorType.RESIDENT, expected_version=2)

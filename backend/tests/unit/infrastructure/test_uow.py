"""Unit evidence for UoW exit behavior and stable database error translation."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.application.errors import (
    ActiveAppointmentExists,
    AppointmentTimeConflict,
    DatabaseIntegrityFailure,
)
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork, _translate_integrity_error
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class FakeDatabaseError(Exception):
    def __init__(self, *, sqlstate: str | None = None, constraint_name: str | None = None) -> None:
        self.sqlstate = sqlstate
        self.constraint_name = constraint_name
        super().__init__("localized message text must not affect mapping")


def _integrity_error(
    *, sqlstate: str | None = None, constraint_name: str | None = None
) -> IntegrityError:
    return IntegrityError(
        "statement",
        {},
        FakeDatabaseError(sqlstate=sqlstate, constraint_name=constraint_name),
    )


@pytest.mark.parametrize(
    ("constraint", "error_type", "code"),
    [
        (
            "uq_appointments_ticket_booked",
            ActiveAppointmentExists,
            "active_appointment_exists",
        ),
        (
            "ex_appointments_worker_booked_overlap",
            AppointmentTimeConflict,
            "appointment_time_conflict",
        ),
    ],
)
def test_named_constraints_map_without_reading_message_text(
    constraint: str,
    error_type: type[Exception],
    code: str,
) -> None:
    translated = _translate_integrity_error(
        _integrity_error(sqlstate="23505", constraint_name=constraint)
    )
    assert isinstance(translated, error_type)
    assert translated.code == code
    assert translated.context == {"constraint": constraint, "sqlstate": "23505"}


@pytest.mark.parametrize(
    ("sqlstate", "code"),
    [
        ("23505", "database_unique_conflict"),
        ("23P01", "database_exclusion_conflict"),
        ("23503", "database_foreign_key_conflict"),
        ("23514", "database_check_conflict"),
        ("XX999", "database_integrity_failure"),
        (None, "database_integrity_failure"),
    ],
)
def test_sqlstate_fallbacks_are_stable(sqlstate: str | None, code: str) -> None:
    translated = _translate_integrity_error(_integrity_error(sqlstate=sqlstate))
    assert type(translated) is DatabaseIntegrityFailure
    assert translated.code == code


@pytest.mark.asyncio
async def test_commit_failure_never_marks_uow_successful_and_preserves_cause() -> None:
    original = _integrity_error(sqlstate="23505")
    session = MagicMock(spec=AsyncSession)
    session.commit = AsyncMock(side_effect=original)
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    factory = cast(async_sessionmaker[AsyncSession], cast(Any, lambda: session))
    uow = SqlAlchemyUnitOfWork(factory)

    with pytest.raises(DatabaseIntegrityFailure) as raised:
        async with uow:
            await uow.commit()

    assert raised.value.code == "database_unique_conflict"
    assert raised.value.__cause__ is original
    assert not uow._committed
    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()

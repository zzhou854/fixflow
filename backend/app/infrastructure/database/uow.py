"""Real SQLAlchemy transaction boundary and named database error mapping."""

from types import TracebackType

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.errors import (
    ActiveAppointmentExists,
    AppointmentTimeConflict,
    DatabaseIntegrityFailure,
    PersistenceConflict,
)
from app.application.ports import (
    AppointmentRepository,
    IdempotencyRepository,
    TicketRepository,
    WorkerEventRepository,
)
from app.infrastructure.database.repositories import (
    SqlAlchemyAppointmentRepository,
    SqlAlchemyIdempotencyRepository,
    SqlAlchemyTicketRepository,
    SqlAlchemyWorkerEventRepository,
)


def _translate_integrity_error(error: IntegrityError) -> PersistenceConflict:
    original = error.orig
    candidates = (
        original,
        original.__cause__ if original is not None else None,
        original.__context__ if original is not None else None,
    )
    sqlstate = next(
        (
            str(value)
            for candidate in candidates
            if candidate is not None
            for value in (getattr(candidate, "sqlstate", None), getattr(candidate, "pgcode", None))
            if value is not None
        ),
        None,
    )
    constraint = next(
        (
            str(value)
            for candidate in candidates
            if candidate is not None
            for value in (getattr(candidate, "constraint_name", None),)
            if value is not None
        ),
        None,
    )
    constraints: dict[str, tuple[type[PersistenceConflict], str]] = {
        "uq_appointments_ticket_booked": (
            ActiveAppointmentExists,
            "active_appointment_exists",
        ),
        "ex_appointments_worker_booked_overlap": (
            AppointmentTimeConflict,
            "appointment_time_conflict",
        ),
        "uq_appointments_supersedes_appointment_id": (
            PersistenceConflict,
            "reschedule_conflict",
        ),
        "uq_worker_events_appointment_sequence": (
            PersistenceConflict,
            "worker_event_sequence_conflict",
        ),
        "uq_worker_events_external_event_key": (
            PersistenceConflict,
            "worker_event_identity_conflict",
        ),
    }
    mapped = constraints.get(constraint or "")
    if mapped is not None:
        error_type, code = mapped
        return error_type(code, constraint=constraint, sqlstate=sqlstate)
    state_codes = {
        "23505": "database_unique_conflict",
        "23P01": "database_exclusion_conflict",
        "23503": "database_foreign_key_conflict",
        "23514": "database_check_conflict",
    }
    return DatabaseIntegrityFailure(
        state_codes.get(sqlstate or "", "database_integrity_failure"),
        sqlstate=sqlstate,
        constraint=constraint,
    )


class SqlAlchemyUnitOfWork:
    """One fresh session and one atomic transaction per application command."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self.session = self._session_factory()
        self._committed = False
        self.tickets: TicketRepository = SqlAlchemyTicketRepository(self.session)
        self.appointments: AppointmentRepository = SqlAlchemyAppointmentRepository(self.session)
        self.worker_events: WorkerEventRepository = SqlAlchemyWorkerEventRepository(self.session)
        self.idempotency: IdempotencyRepository = SqlAlchemyIdempotencyRepository(self.session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._committed:
            await self.session.rollback()
        await self.session.close()

    async def flush(self) -> None:
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from exc

    async def commit(self) -> None:
        try:
            await self.session.commit()
            self._committed = True
        except IntegrityError as exc:
            raise _translate_integrity_error(exc) from exc

    async def rollback(self) -> None:
        await self.session.rollback()

"""Focused SQLAlchemy repository implementations grouped by aggregate."""

from app.infrastructure.database.repositories.appointment import (
    SqlAlchemyAppointmentRepository,
)
from app.infrastructure.database.repositories.idempotency import (
    SqlAlchemyIdempotencyRepository,
)
from app.infrastructure.database.repositories.outbox import SqlAlchemyOutboxRepository
from app.infrastructure.database.repositories.policy import SqlAlchemyPolicyRepository
from app.infrastructure.database.repositories.query import SqlAlchemyQueryRepository
from app.infrastructure.database.repositories.ticket import SqlAlchemyTicketRepository
from app.infrastructure.database.repositories.worker_event import (
    SqlAlchemyWorkerEventRepository,
)

__all__ = [
    "SqlAlchemyAppointmentRepository",
    "SqlAlchemyIdempotencyRepository",
    "SqlAlchemyOutboxRepository",
    "SqlAlchemyPolicyRepository",
    "SqlAlchemyQueryRepository",
    "SqlAlchemyTicketRepository",
    "SqlAlchemyWorkerEventRepository",
]

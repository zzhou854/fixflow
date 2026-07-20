"""Import all SQLAlchemy mappings so Base.metadata is complete."""

from app.infrastructure.database.models.appointment import (
    Appointment,
    AppointmentStatusHistory,
)
from app.infrastructure.database.models.event import WorkerEvent
from app.infrastructure.database.models.idempotency import (
    IdempotencyExecutionStatus,
    IdempotencyRecord,
)
from app.infrastructure.database.models.policy import PolicyChunk, PolicyDocument
from app.infrastructure.database.models.ticket import RepairTicket, TicketStatusHistory
from app.infrastructure.database.models.user import Property, ResidentPropertyRelation, User
from app.infrastructure.database.models.worker import Worker, WorkerAvailability, WorkerSkill

__all__ = [
    "Appointment",
    "AppointmentStatusHistory",
    "IdempotencyExecutionStatus",
    "IdempotencyRecord",
    "Property",
    "PolicyChunk",
    "PolicyDocument",
    "RepairTicket",
    "ResidentPropertyRelation",
    "TicketStatusHistory",
    "User",
    "Worker",
    "WorkerAvailability",
    "WorkerEvent",
    "WorkerSkill",
]

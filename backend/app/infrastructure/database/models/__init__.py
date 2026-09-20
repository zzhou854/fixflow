"""Import all SQLAlchemy mappings so Base.metadata is complete."""

from app.infrastructure.database.models.agent_control import (
    AgentMessageRow,
    AgentThreadRecordRow,
    HumanReviewCaseEventRow,
    HumanReviewCaseRow,
)
from app.infrastructure.database.models.appointment import (
    Appointment,
    AppointmentStatusHistory,
)
from app.infrastructure.database.models.event import WorkerEvent
from app.infrastructure.database.models.idempotency import (
    IdempotencyExecutionStatus,
    IdempotencyRecord,
)
from app.infrastructure.database.models.observability import (
    AgentRun,
    AgentRunStatus,
    AgentRunTrigger,
    AgentTraceEvent,
    OutboxEvent,
    OutboxStatus,
    TraceSource,
)
from app.infrastructure.database.models.online import LLMShadowResultStatus, LLMShadowRun
from app.infrastructure.database.models.policy import PolicyChunk, PolicyDocument
from app.infrastructure.database.models.reconciliation import (
    EvidenceStatus,
    OperationReconciliationCase,
    ReconciliationAction,
    ReconciliationStatus,
)
from app.infrastructure.database.models.replay import (
    AgentReplayBundle,
    AgentReplayExecution,
    AgentReplayStep,
)
from app.infrastructure.database.models.ticket import RepairTicket, TicketStatusHistory
from app.infrastructure.database.models.user import Property, ResidentPropertyRelation, User
from app.infrastructure.database.models.worker import Worker, WorkerAvailability, WorkerSkill

__all__ = [
    "Appointment",
    "AppointmentStatusHistory",
    "AgentMessageRow",
    "AgentRun",
    "AgentRunStatus",
    "AgentRunTrigger",
    "AgentReplayBundle",
    "AgentReplayExecution",
    "AgentReplayStep",
    "AgentTraceEvent",
    "AgentThreadRecordRow",
    "IdempotencyExecutionStatus",
    "IdempotencyRecord",
    "HumanReviewCaseEventRow",
    "HumanReviewCaseRow",
    "OutboxEvent",
    "OutboxStatus",
    "LLMShadowResultStatus",
    "LLMShadowRun",
    "OperationReconciliationCase",
    "ReconciliationAction",
    "ReconciliationStatus",
    "EvidenceStatus",
    "Property",
    "PolicyChunk",
    "PolicyDocument",
    "RepairTicket",
    "ResidentPropertyRelation",
    "TicketStatusHistory",
    "TraceSource",
    "User",
    "Worker",
    "WorkerAvailability",
    "WorkerEvent",
    "WorkerSkill",
]

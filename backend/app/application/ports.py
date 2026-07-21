"""Narrow persistence ports required by approved FixFlow use cases."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Any, Protocol
from uuid import UUID

from app.application.query_models import (
    ResidentPropertyReadModel,
    SlotWorkerSource,
    TicketDetailReadModel,
    TicketListItemReadModel,
)
from app.domain.enums import (
    ActorType,
    AppointmentStatus,
    IssueCategory,
    Severity,
    TicketStatus,
    WorkerSkillType,
)
from app.domain.models import (
    AppointmentDraft,
    AppointmentSnapshot,
    TicketSnapshot,
    WorkerEventSnapshot,
)


@dataclass(frozen=True, slots=True)
class TicketHistoryRecord:
    ticket_id: UUID
    from_status: TicketStatus | None
    to_status: TicketStatus
    action: str
    actor_type: ActorType
    actor_id: UUID
    trace_id: UUID
    version_before: int
    version_after: int
    reason_code: str | None = None
    reason_text: str | None = None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AppointmentHistoryRecord:
    appointment_id: UUID
    from_status: AppointmentStatus | None
    to_status: AppointmentStatus
    actor_type: ActorType
    actor_id: UUID
    trace_id: UUID
    version_before: int
    version_after: int
    occurred_at: datetime
    reason_code: str | None = None
    reason_text: str | None = None
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkerEventRecord:
    event_id: UUID
    appointment_id: UUID
    subject_worker_id: UUID
    sequence_no: int
    event_type: str
    actor_type: ActorType
    actor_id: UUID
    external_event_key: str
    request_hash: str
    trace_id: UUID
    occurred_at: datetime
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StoredIdempotency:
    is_new: bool
    request_hash: str
    execution_status: str
    resource_type: str | None = None
    resource_id: UUID | None = None
    response_payload: dict[str, Any] | None = None


class TicketRepository(Protocol):
    async def get(self, ticket_id: UUID, *, for_update: bool = False) -> TicketSnapshot | None: ...
    async def resident_has_property(self, resident_id: UUID, property_id: UUID) -> bool: ...
    async def actor_is_operator(self, actor_id: UUID) -> bool: ...
    async def has_exact_open_match(
        self, resident_id: UUID, property_id: UUID, category: IssueCategory, location: str
    ) -> bool: ...
    async def find_exact_open_matches(
        self, resident_id: UUID, property_id: UUID, category: IssueCategory, location: str
    ) -> Sequence[TicketSnapshot]: ...
    async def add(self, snapshot: TicketSnapshot) -> None: ...
    async def update(self, snapshot: TicketSnapshot, *, expected_version: int) -> None: ...
    async def add_history(self, record: TicketHistoryRecord) -> None: ...


class AppointmentRepository(Protocol):
    async def get(
        self, appointment_id: UUID, *, for_update: bool = False
    ) -> AppointmentSnapshot | None: ...
    async def latest_for_ticket(self, ticket_id: UUID) -> AppointmentSnapshot | None: ...
    async def active_for_ticket(self, ticket_id: UUID) -> AppointmentSnapshot | None: ...
    async def worker_can_service(
        self,
        worker_id: UUID,
        property_id: UUID,
        skill: WorkerSkillType,
        starts_at: datetime,
        ends_at: datetime,
    ) -> bool: ...
    async def add(self, draft: AppointmentDraft) -> None: ...
    async def update(
        self,
        snapshot: AppointmentSnapshot,
        *,
        expected_version: int,
        outcome: dict[str, Any] | None = None,
    ) -> None: ...
    async def add_history(self, record: AppointmentHistoryRecord) -> None: ...


class WorkerEventRepository(Protocol):
    async def list_for_appointment(self, appointment_id: UUID) -> Sequence[WorkerEventSnapshot]: ...
    async def get_by_external_key(self, key: str) -> WorkerEventSnapshot | None: ...
    async def add(self, record: WorkerEventRecord) -> None: ...


class IdempotencyRepository(Protocol):
    async def acquire(
        self,
        *,
        scope: str,
        actor_type: ActorType,
        actor_id: UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> StoredIdempotency: ...
    async def succeed(
        self,
        *,
        scope: str,
        actor_type: ActorType,
        actor_id: UUID,
        idempotency_key: str,
        resource_type: str,
        resource_id: UUID,
        response_payload: dict[str, Any],
    ) -> None: ...


class QueryRepository(Protocol):
    async def get_property(
        self, resident_id: UUID, property_id: UUID
    ) -> ResidentPropertyReadModel | None: ...
    async def get_property_service_area(self, property_id: UUID) -> str | None: ...
    async def list_slot_worker_sources(
        self,
        *,
        skill: WorkerSkillType,
        search_window_start: datetime,
        search_window_end: datetime,
    ) -> Sequence[SlotWorkerSource]: ...
    async def list_resident_properties(
        self, resident_id: UUID
    ) -> Sequence[ResidentPropertyReadModel]: ...
    async def list_resident_tickets(
        self, resident_id: UUID, *, limit: int, offset: int
    ) -> Sequence[TicketListItemReadModel]: ...
    async def list_operator_tickets(
        self,
        *,
        ticket_status: TicketStatus | None,
        issue_category: IssueCategory | None,
        severity: Severity | None,
        limit: int,
        offset: int,
    ) -> Sequence[TicketListItemReadModel]: ...
    async def get_ticket_detail(self, ticket_id: UUID) -> TicketDetailReadModel | None: ...


class UnitOfWork(Protocol):
    tickets: TicketRepository
    appointments: AppointmentRepository
    worker_events: WorkerEventRepository
    idempotency: IdempotencyRepository
    queries: QueryRepository

    async def __aenter__(self) -> UnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    async def flush(self) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class UnitOfWorkFactory(Protocol):
    def __call__(self) -> UnitOfWork: ...

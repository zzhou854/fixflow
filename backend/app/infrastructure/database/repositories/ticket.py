"""SQLAlchemy ticket snapshot, authorization, exact-match, and history persistence."""

from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.errors import PersistenceConflict
from app.application.ports import TicketHistoryRecord
from app.domain.enums import IssueCategory, TicketStatus
from app.domain.models import TicketSnapshot
from app.infrastructure.database.mappers import ticket_to_snapshot
from app.infrastructure.database.models.ticket import RepairTicket, TicketStatusHistory
from app.infrastructure.database.models.user import ResidentPropertyRelation, User


class SqlAlchemyTicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, ticket_id: UUID, *, for_update: bool = False) -> TicketSnapshot | None:
        statement = select(RepairTicket).where(RepairTicket.id == ticket_id)
        if for_update:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        return ticket_to_snapshot(row) if row is not None else None

    async def resident_has_property(self, resident_id: UUID, property_id: UUID) -> bool:
        statement = select(
            select(ResidentPropertyRelation.id)
            .where(
                ResidentPropertyRelation.resident_id == resident_id,
                ResidentPropertyRelation.property_id == property_id,
                ResidentPropertyRelation.is_active.is_(True),
            )
            .exists()
        )
        return bool(await self._session.scalar(statement))

    async def actor_is_operator(self, actor_id: UUID) -> bool:
        statement = select(
            select(User.id)
            .where(User.id == actor_id, User.role == "OPERATOR", User.is_active.is_(True))
            .exists()
        )
        return bool(await self._session.scalar(statement))

    async def has_exact_open_match(
        self, resident_id: UUID, property_id: UUID, category: IssueCategory, location: str
    ) -> bool:
        active = tuple(
            status
            for status in TicketStatus
            if status not in {TicketStatus.CLOSED, TicketStatus.CANCELLED}
        )
        statement = select(
            select(RepairTicket.id)
            .where(
                RepairTicket.resident_id == resident_id,
                RepairTicket.property_id == property_id,
                RepairTicket.issue_category == category,
                func.lower(func.btrim(RepairTicket.issue_location)) == location.strip().lower(),
                RepairTicket.status.in_(active),
            )
            .exists()
        )
        return bool(await self._session.scalar(statement))

    async def find_exact_open_matches(
        self, resident_id: UUID, property_id: UUID, category: IssueCategory, location: str
    ) -> list[TicketSnapshot]:
        active = tuple(
            status
            for status in TicketStatus
            if status not in {TicketStatus.CLOSED, TicketStatus.CANCELLED}
        )
        rows = await self._session.scalars(
            select(RepairTicket)
            .where(
                RepairTicket.resident_id == resident_id,
                RepairTicket.property_id == property_id,
                RepairTicket.issue_category == category,
                func.lower(func.btrim(RepairTicket.issue_location)) == location.strip().lower(),
                RepairTicket.status.in_(active),
            )
            .order_by(RepairTicket.created_at, RepairTicket.id)
        )
        return [ticket_to_snapshot(row) for row in rows]

    async def add(self, snapshot: TicketSnapshot) -> None:
        self._session.add(
            RepairTicket(
                id=snapshot.ticket_id,
                resident_id=snapshot.resident_id,
                property_id=snapshot.property_id,
                issue_category=snapshot.issue_category,
                issue_location=snapshot.issue_location,
                issue_description=snapshot.issue_description,
                severity=snapshot.severity,
                status=snapshot.status,
                escalated_from_status=snapshot.escalated_from_status,
                rework_count=snapshot.rework_count,
                version=snapshot.version,
                cancelled_at=snapshot.cancelled_at,
                closed_at=snapshot.closed_at,
            )
        )

    async def update(self, snapshot: TicketSnapshot, *, expected_version: int) -> None:
        if snapshot.version != expected_version + 1:
            raise PersistenceConflict("invalid_version_step")
        result = await self._session.execute(
            update(RepairTicket)
            .where(RepairTicket.id == snapshot.ticket_id, RepairTicket.version == expected_version)
            .values(
                status=snapshot.status,
                escalated_from_status=snapshot.escalated_from_status,
                rework_count=snapshot.rework_count,
                version=RepairTicket.version + 1,
                cancelled_at=snapshot.cancelled_at,
                closed_at=snapshot.closed_at,
                updated_at=func.now(),
            )
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            raise PersistenceConflict(
                "version_conflict", resource_type="repair_ticket", resource_id=snapshot.ticket_id
            )

    async def add_history(self, record: TicketHistoryRecord) -> None:
        self._session.add(
            TicketStatusHistory(
                ticket_id=record.ticket_id,
                from_status=record.from_status,
                to_status=record.to_status,
                action=record.action,
                actor_type=record.actor_type,
                actor_id=str(record.actor_id),
                reason_code=record.reason_code,
                reason_text=record.reason_text,
                evidence={"items": list(record.evidence)},
                trace_id=record.trace_id,
                version_before=record.version_before,
                version_after=record.version_after,
            )
        )

"""Sanitized operator reconciliation query/action service."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import ColumnElement

from app.api.schemas.reconciliation import ReconciliationCaseResponse
from app.infrastructure.database.models.reconciliation import (
    OperationReconciliationCase,
    ReconciliationAction,
    ReconciliationStatus,
)
from app.infrastructure.database.models.user import ResidentPropertyRelation, User


class OperatorReconciliationService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @staticmethod
    def _safe(row: OperationReconciliationCase) -> ReconciliationCaseResponse:
        return ReconciliationCaseResponse(
            case_id=row.id,
            operation_id_short=str(row.operation_id)[:8],
            thread_id_short=str(row.thread_id)[:8] if row.thread_id else None,
            original_run_id_short=str(row.original_run_id)[:8],
            operation_type=row.action,
            status=row.status,
            target_entity_type=row.target_entity_type,
            target_entity_id=row.target_entity_id,
            expected_entity_version=row.expected_entity_version,
            attempt_count=row.attempt_count,
            evidence_status=row.last_evidence_status.value if row.last_evidence_status else None,
            last_error_code=row.last_error_code,
            resolution_code=row.resolution_code,
            safe_result=row.safe_result,
            retry_allowed=row.status is ReconciliationStatus.PENDING,
            created_at=row.created_at,
            updated_at=row.updated_at,
            resolved_at=row.resolved_at,
        )

    async def _operator_active(self, session: AsyncSession, operator_id: UUID) -> bool:
        user = await session.get(User, operator_id)
        return bool(user and user.is_active and user.role == "OPERATOR")

    @staticmethod
    def _authorized_cases() -> ColumnElement[bool]:
        resident_cases = OperationReconciliationCase.id.in_(
            select(OperationReconciliationCase.id)
            .join(
                ResidentPropertyRelation,
                (ResidentPropertyRelation.resident_id == OperationReconciliationCase.user_id)
                & (ResidentPropertyRelation.property_id == OperationReconciliationCase.property_id)
                & ResidentPropertyRelation.is_active.is_(True),
            )
            .join(User, User.id == OperationReconciliationCase.user_id)
            .where(
                OperationReconciliationCase.actor_type == "RESIDENT",
                OperationReconciliationCase.actor_id == OperationReconciliationCase.user_id,
                User.is_active.is_(True),
                User.role == "RESIDENT",
            )
        )
        return or_(
            resident_cases,
            OperationReconciliationCase.actor_type == "OPERATOR",
        )

    async def list(
        self,
        *,
        operator_id: UUID,
        status: ReconciliationStatus | None,
        action: ReconciliationAction | None,
        limit: int,
        offset: int,
    ) -> tuple[ReconciliationCaseResponse, ...]:
        async with self._sessions() as session:
            if not await self._operator_active(session, operator_id):
                return ()
            statement = select(OperationReconciliationCase).where(self._authorized_cases())
            if status:
                statement = statement.where(OperationReconciliationCase.status == status)
            if action:
                statement = statement.where(OperationReconciliationCase.action == action)
            rows = await session.scalars(
                statement.order_by(
                    OperationReconciliationCase.created_at.desc(),
                    OperationReconciliationCase.id.desc(),
                )
                .limit(limit)
                .offset(offset)
            )
            return tuple(self._safe(row) for row in rows)

    async def get(self, case_id: UUID, *, operator_id: UUID) -> ReconciliationCaseResponse | None:
        async with self._sessions() as session:
            if not await self._operator_active(session, operator_id):
                return None
            row = await session.scalar(
                select(OperationReconciliationCase).where(
                    OperationReconciliationCase.id == case_id, self._authorized_cases()
                )
            )
            return self._safe(row) if row else None

    async def recheck(
        self, case_id: UUID, *, operator_id: UUID
    ) -> ReconciliationCaseResponse | None:
        now = datetime.now(UTC)
        async with self._sessions() as session, session.begin():
            if not await self._operator_active(session, operator_id):
                return None
            row = await session.scalar(
                update(OperationReconciliationCase)
                .where(
                    OperationReconciliationCase.id == case_id,
                    OperationReconciliationCase.status == ReconciliationStatus.PENDING,
                    self._authorized_cases(),
                )
                .values(available_at=now, updated_at=now)
                .returning(OperationReconciliationCase)
            )
            return self._safe(row) if row else None

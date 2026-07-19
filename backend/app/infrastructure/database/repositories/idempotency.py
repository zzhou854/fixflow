"""PostgreSQL request-idempotency acquisition and durable success results."""

from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.ports import StoredIdempotency
from app.domain.enums import ActorType
from app.infrastructure.database.models.idempotency import (
    IdempotencyExecutionStatus,
    IdempotencyRecord,
)


class SqlAlchemyIdempotencyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def acquire(
        self,
        *,
        scope: str,
        actor_type: ActorType,
        actor_id: UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> StoredIdempotency:
        statement = (
            insert(IdempotencyRecord)
            .values(
                id=uuid4(),
                scope=scope,
                actor_type=actor_type,
                actor_id=str(actor_id),
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                execution_status=IdempotencyExecutionStatus.PENDING,
            )
            .on_conflict_do_nothing(constraint="uq_idempotency_records_operation_actor_key")
            .returning(IdempotencyRecord.id)
        )
        created = await self._session.scalar(statement)
        row = await self._session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.actor_type == actor_type,
                IdempotencyRecord.actor_id == str(actor_id),
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
        )
        if row is None:
            raise RuntimeError("idempotency acquisition did not produce a record")
        return StoredIdempotency(
            is_new=created is not None,
            request_hash=row.request_hash,
            execution_status=row.execution_status.value,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            response_payload=row.response_payload,
        )

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
    ) -> None:
        await self._session.execute(
            update(IdempotencyRecord)
            .where(
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.actor_type == actor_type,
                IdempotencyRecord.actor_id == str(actor_id),
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
            .values(
                execution_status=IdempotencyExecutionStatus.SUCCEEDED,
                resource_type=resource_type,
                resource_id=resource_id,
                response_payload=response_payload,
                updated_at=func.now(),
            )
        )

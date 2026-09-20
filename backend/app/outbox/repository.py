"""Short PostgreSQL transactions for claiming and acknowledging outbox rows."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.models.observability import OutboxEvent, OutboxStatus
from app.outbox.models import ClaimedOutboxEvent


class OutboxLeaseLost(RuntimeError):
    """A stale worker attempted to acknowledge a superseded outbox claim."""

    code = "STALE_OUTBOX_CLAIM"


class SqlAlchemyOutboxDispatchRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def claim_batch(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease_seconds: int,
        batch_size: int,
    ) -> tuple[ClaimedOutboxEvent, ...]:
        async with self._sessions() as session, session.begin():
            eligible = or_(
                and_(
                    OutboxEvent.status == OutboxStatus.PENDING,
                    OutboxEvent.available_at <= now,
                ),
                and_(
                    OutboxEvent.status == OutboxStatus.PROCESSING,
                    OutboxEvent.claim_expires_at <= now,
                ),
            )
            rows = tuple(
                await session.scalars(
                    select(OutboxEvent)
                    .where(eligible)
                    .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            )
            expires = now + timedelta(seconds=lease_seconds)
            for row in rows:
                row.status = OutboxStatus.PROCESSING
                row.claimed_by = worker_id
                # Every acquisition, including a reclaimed expired lease, has a
                # fresh fencing token. A reused worker id is therefore not enough
                # to acknowledge a newer claim.
                row.claim_token = uuid4()
                row.claim_expires_at = expires
            await session.flush()
            return tuple(ClaimedOutboxEvent.model_validate(row) for row in rows)

    async def mark_dispatched(
        self,
        event_id: UUID,
        *,
        worker_id: str,
        claim_token: UUID,
        occurred_at: datetime,
    ) -> None:
        async with self._sessions() as session, session.begin():
            result = await session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.status == OutboxStatus.PROCESSING,
                    OutboxEvent.claimed_by == worker_id,
                    OutboxEvent.claim_token == claim_token,
                )
                .values(
                    status=OutboxStatus.DISPATCHED,
                    dispatched_at=occurred_at,
                    claimed_by=None,
                    claim_token=None,
                    claim_expires_at=None,
                    last_error_code=None,
                    last_error_message=None,
                )
                .returning(OutboxEvent.id)
            )
            if result.scalar_one_or_none() is None:
                raise OutboxLeaseLost("STALE_OUTBOX_CLAIM")

    async def mark_failed(
        self,
        event_id: UUID,
        *,
        worker_id: str,
        claim_token: UUID,
        occurred_at: datetime,
        error_code: str,
        error_message: str,
        max_attempts: int,
        retry_base_seconds: float,
    ) -> OutboxStatus:
        async with self._sessions() as session, session.begin():
            next_attempt = OutboxEvent.attempt_count + 1
            should_dead_letter = next_attempt >= max_attempts
            result = await session.execute(
                update(OutboxEvent)
                .where(
                    OutboxEvent.id == event_id,
                    OutboxEvent.status == OutboxStatus.PROCESSING,
                    OutboxEvent.claimed_by == worker_id,
                    OutboxEvent.claim_token == claim_token,
                )
                .values(
                    attempt_count=next_attempt,
                    last_error_code=error_code[:80],
                    last_error_message=error_message[:1000],
                    claimed_by=None,
                    claim_token=None,
                    claim_expires_at=None,
                    status=case(
                        (should_dead_letter, OutboxStatus.DEAD_LETTER),
                        else_=OutboxStatus.PENDING,
                    ),
                    available_at=case(
                        (should_dead_letter, OutboxEvent.available_at),
                        else_=occurred_at
                        + retry_base_seconds
                        * func.power(2, OutboxEvent.attempt_count)
                        * text("INTERVAL '1 second'"),
                    ),
                )
                .returning(OutboxEvent.status, OutboxEvent.attempt_count)
            )
            row = result.one_or_none()
            if row is None:
                raise OutboxLeaseLost("STALE_OUTBOX_CLAIM")
            status, _attempt_count = row
            return OutboxStatus(status)

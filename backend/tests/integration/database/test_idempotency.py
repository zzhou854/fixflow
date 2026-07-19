"""Database uniqueness tests for durable idempotency identities."""

from uuid import uuid4

import asyncpg
import pytest


async def _insert(
    connection: asyncpg.Connection,
    *,
    scope: str,
    actor_id: str,
    key: str,
) -> None:
    await connection.execute(
        "INSERT INTO idempotency_records "
        "(id, scope, actor_type, actor_id, idempotency_key, request_hash, execution_status) "
        "VALUES ($1, $2, 'SYSTEM', $3, $4, $5, 'PENDING')",
        uuid4(),
        scope,
        actor_id,
        key,
        uuid4().hex,
    )


@pytest.mark.asyncio
async def test_same_operation_actor_and_key_is_unique(
    db_connection: asyncpg.Connection,
) -> None:
    await _insert(db_connection, scope="ticket.create", actor_id="SYSTEM", key="same")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _insert(db_connection, scope="ticket.create", actor_id="SYSTEM", key="same")


@pytest.mark.asyncio
async def test_different_actor_can_reuse_key(db_connection: asyncpg.Connection) -> None:
    await _insert(db_connection, scope="ticket.create", actor_id="SYSTEM-A", key="same")
    await _insert(db_connection, scope="ticket.create", actor_id="SYSTEM-B", key="same")


@pytest.mark.asyncio
async def test_different_scope_can_reuse_key(db_connection: asyncpg.Connection) -> None:
    await _insert(db_connection, scope="ticket.create", actor_id="SYSTEM", key="same")
    await _insert(db_connection, scope="appointment.book", actor_id="SYSTEM", key="same")


@pytest.mark.asyncio
async def test_system_actor_id_cannot_be_null(db_connection: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.NotNullViolationError):
        await db_connection.execute(
            "INSERT INTO idempotency_records "
            "(id, scope, actor_type, actor_id, idempotency_key, request_hash) "
            "VALUES ($1, 'ticket.create', 'SYSTEM', NULL, 'key', $2)",
            uuid4(),
            uuid4().hex,
        )

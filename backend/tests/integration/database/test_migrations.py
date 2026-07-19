"""Alembic lifecycle tests against a disposable PostgreSQL database."""

import asyncio

import asyncpg
from alembic import command
from alembic.config import Config

EXPECTED_TABLES = {
    "users",
    "properties",
    "resident_property_relations",
    "repair_tickets",
    "ticket_status_history",
    "workers",
    "worker_skills",
    "worker_availability",
    "appointments",
    "appointment_status_history",
    "worker_events",
    "idempotency_records",
}


async def _public_tables(database_url: str) -> set[str]:
    connection = await asyncpg.connect(
        database_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        rows = await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {str(row["tablename"]) for row in rows}
    finally:
        await connection.close()


def test_upgrade_downgrade_upgrade_cycle(empty_database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", empty_database_url)

    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= asyncio.run(_public_tables(empty_database_url))

    command.downgrade(config, "base")
    assert EXPECTED_TABLES.isdisjoint(asyncio.run(_public_tables(empty_database_url)))

    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= asyncio.run(_public_tables(empty_database_url))

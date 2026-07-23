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
    "policy_documents",
    "policy_chunks",
    "agent_runs",
    "agent_trace_events",
    "outbox_events",
    "operation_reconciliation_cases",
    "agent_replay_bundles",
    "agent_replay_steps",
    "agent_replay_executions",
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


async def _task4_audit_columns(database_url: str) -> set[tuple[str, str, str]]:
    connection = await asyncpg.connect(
        database_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    try:
        rows = await connection.fetch(
            "SELECT table_name, column_name, is_nullable "
            "FROM information_schema.columns "
            "WHERE (table_name = 'appointment_status_history' AND column_name = 'trace_id') "
            "OR (table_name = 'worker_events' AND column_name = 'trace_id')"
        )
        return {
            (str(row["table_name"]), str(row["column_name"]), str(row["is_nullable"]))
            for row in rows
        }
    finally:
        await connection.close()


def test_upgrade_downgrade_upgrade_cycle(empty_database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", empty_database_url)

    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= asyncio.run(_public_tables(empty_database_url))
    assert asyncio.run(_task4_audit_columns(empty_database_url)) == {
        ("appointment_status_history", "trace_id", "NO"),
        ("worker_events", "trace_id", "NO"),
    }

    command.downgrade(config, "20260722_0005")
    assert {
        "agent_replay_bundles",
        "agent_replay_steps",
        "agent_replay_executions",
    }.isdisjoint(asyncio.run(_public_tables(empty_database_url)))
    assert "operation_reconciliation_cases" in asyncio.run(_public_tables(empty_database_url))
    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= asyncio.run(_public_tables(empty_database_url))

    command.downgrade(config, "20260721_0004")
    assert "operation_reconciliation_cases" not in asyncio.run(_public_tables(empty_database_url))
    assert {"agent_runs", "agent_trace_events", "outbox_events"} <= asyncio.run(
        _public_tables(empty_database_url)
    )
    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= asyncio.run(_public_tables(empty_database_url))

    command.downgrade(config, "20260720_0003")
    assert {"agent_runs", "agent_trace_events", "outbox_events"}.isdisjoint(
        asyncio.run(_public_tables(empty_database_url))
    )
    assert {"policy_documents", "policy_chunks"} <= asyncio.run(_public_tables(empty_database_url))
    assert asyncio.run(_task4_audit_columns(empty_database_url)) == {
        ("appointment_status_history", "trace_id", "NO"),
        ("worker_events", "trace_id", "NO"),
    }

    command.upgrade(config, "head")
    assert EXPECTED_TABLES <= asyncio.run(_public_tables(empty_database_url))
    assert asyncio.run(_task4_audit_columns(empty_database_url)) == {
        ("appointment_status_history", "trace_id", "NO"),
        ("worker_events", "trace_id", "NO"),
    }
    command.check(config)

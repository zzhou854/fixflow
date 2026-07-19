"""Isolated PostgreSQL databases for persistence integration tests."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from app.config import get_settings
from sqlalchemy.engine import URL, make_url


def _asyncpg_url(url: URL) -> str:
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


async def _create_database(admin_url: str, database_name: str) -> None:
    connection = await asyncpg.connect(admin_url)
    try:
        await connection.execute(f'CREATE DATABASE "{database_name}"')
    finally:
        await connection.close()


async def _drop_database(admin_url: str, database_name: str) -> None:
    connection = await asyncpg.connect(admin_url)
    try:
        await connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            database_name,
        )
        await connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
    finally:
        await connection.close()


@contextmanager
def _temporary_database() -> Iterator[str]:
    base_url = make_url(get_settings().database_url.get_secret_value())
    database_name = f"fixflow_migration_test_{uuid4().hex}"
    admin_url = _asyncpg_url(base_url.set(database="postgres"))
    test_url = base_url.set(database=database_name).render_as_string(hide_password=False)
    asyncio.run(_create_database(admin_url, database_name))
    try:
        yield test_url
    finally:
        asyncio.run(_drop_database(admin_url, database_name))


def _migrate(database_url: str, revision: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, revision) if revision != "base" else command.downgrade(config, "base")


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    """A random migrated database shared by transactional constraint tests."""

    try:
        with _temporary_database() as database_url:
            _migrate(database_url, "head")
            yield database_url
    except (OSError, asyncpg.PostgresConnectionError) as exc:
        pytest.skip(f"PostgreSQL integration database unavailable: {type(exc).__name__}")


@pytest.fixture
def empty_database_url() -> Iterator[str]:
    """A separate empty database for destructive migration-cycle tests."""

    with _temporary_database() as database_url:
        yield database_url


@pytest_asyncio.fixture
async def db_connection(migrated_database_url: str) -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(
        migrated_database_url.replace("postgresql+asyncpg://", "postgresql://")
    )
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()

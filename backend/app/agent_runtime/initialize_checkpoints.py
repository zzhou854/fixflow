"""Create the isolated checkpoint database and official LangGraph tables.

Run explicitly from a development entrypoint; this module never runs during an
ordinary graph turn or on import.
"""

import asyncio
import sys
from typing import cast

from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from app.agent_runtime.checkpoint import open_postgres_checkpointer
from app.config import get_settings


def configure_windows_selector_loop() -> None:
    """Configure psycopg's Windows-compatible policy before ``asyncio.run``."""

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def _ensure_database(checkpoint_url: str) -> None:
    parameters = conninfo_to_dict(checkpoint_url)
    database_name = parameters.pop("dbname", None) or parameters.pop("database", None)
    if database_name is None:
        raise ValueError("CHECKPOINT_DATABASE_URL must include a database name")
    admin_url = make_conninfo(**parameters, dbname="postgres")  # type: ignore[arg-type]
    database_name = cast(str, database_name)
    async with await AsyncConnection.connect(admin_url, autocommit=True) as connection:
        async with connection.cursor() as cursor:
            await cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
            if await cursor.fetchone() is None:
                await cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
                )


async def initialize() -> None:
    """Ensure the database exists, then let the official saver own its schema."""

    settings = get_settings()
    if settings.checkpoint_database_url is None:
        raise ValueError("CHECKPOINT_DATABASE_URL is required")
    url = settings.checkpoint_database_url.get_secret_value()
    await _ensure_database(url)
    async with open_postgres_checkpointer(url):
        # setup() runs exactly once for this explicit initialization command.
        return


def main() -> None:
    configure_windows_selector_loop()
    asyncio.run(initialize())


if __name__ == "__main__":
    main()

"""Official PostgreSQL checkpointer lifecycle, isolated from the business database."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


@asynccontextmanager
async def open_postgres_checkpointer(
    checkpoint_database_url: str,
) -> AsyncIterator[AsyncPostgresSaver]:
    """Open and initialize one saver for the composition-root lifetime."""

    serializer = JsonPlusSerializer(
        pickle_fallback=False,
        allowed_json_modules=(),
        allowed_msgpack_modules=(),
    )
    async with AsyncPostgresSaver.from_conn_string(
        checkpoint_database_url, serde=serializer
    ) as saver:
        await saver.setup()
        yield saver

"""Dedicated outbox dispatcher process entry point."""

from __future__ import annotations

import asyncio
import socket
from contextlib import AsyncExitStack

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.outbox.consumer import TraceDomainEventProjector
from app.outbox.dispatcher import OutboxDispatcher
from app.outbox.repository import SqlAlchemyOutboxDispatchRepository
from app.trace.runtime import TraceRuntime
from app.trace.sanitizer import TraceSanitizer


async def _run() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    trace = TraceRuntime(
        sessions,
        TraceSanitizer(
            max_payload_bytes=settings.trace_max_payload_bytes,
            max_string_length=settings.trace_max_string_length,
        ),
    )
    dispatcher = OutboxDispatcher(
        SqlAlchemyOutboxDispatchRepository(sessions),
        TraceDomainEventProjector(trace),
        worker_id=f"{socket.gethostname()}:{id(engine)}",
        lease_seconds=settings.outbox_lease_seconds,
        batch_size=settings.outbox_batch_size,
        max_attempts=settings.outbox_max_attempts,
        retry_base_seconds=settings.outbox_retry_base_seconds,
    )
    async with AsyncExitStack():
        try:
            await dispatcher.run_forever(settings.outbox_poll_interval_seconds)
        finally:
            await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()

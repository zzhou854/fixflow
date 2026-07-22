"""Dedicated reconciliation worker entry point."""

import asyncio
import socket
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import get_settings
from app.reconciliation.mcp_client import ReconciliationOutcomeMCPClient
from app.reconciliation.repository import SqlAlchemyReconciliationRepository
from app.reconciliation.worker import ReconciliationWorker


async def _run() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with ReconciliationOutcomeMCPClient(settings.property_operations_mcp_url) as outcomes:
        worker = ReconciliationWorker(
            SqlAlchemyReconciliationRepository(sessions),
            outcomes,
            worker_id=f"{socket.gethostname()}:{id(engine)}",
            lease_seconds=settings.reconciliation_lease_seconds,
            max_attempts=settings.reconciliation_max_attempts,
            retry_base_seconds=int(settings.reconciliation_retry_base_seconds),
        )
        try:
            while True:
                await worker.run_once(
                    now=datetime.now(UTC), limit=settings.reconciliation_batch_size
                )
                await asyncio.sleep(settings.reconciliation_poll_interval_seconds)
        finally:
            await engine.dispose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()

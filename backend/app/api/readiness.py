"""Production startup checks that must pass before the API accepts traffic."""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

ROOT_DIR = Path(__file__).resolve().parents[3]


def expected_migration_head() -> str:
    """Return the single migration head frozen in the shipped application."""

    config = Config(str(ROOT_DIR / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    if head is None:
        raise RuntimeError("no Alembic migration head is defined")
    return head


async def verify_database_migration_head(engine: AsyncEngine) -> None:
    """Fail startup when the business database is not exactly at the code head."""

    expected = expected_migration_head()
    try:
        async with engine.connect() as connection:
            actual = (
                await connection.execute(text("SELECT version_num FROM alembic_version"))
            ).scalar_one_or_none()
    except Exception as exc:
        raise RuntimeError("business database migration status could not be verified") from exc
    if actual != expected:
        raise RuntimeError(
            f"business database migration is not current (expected {expected}, got {actual})"
        )

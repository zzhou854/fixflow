import pytest
from app.api.readiness import expected_migration_head, verify_database_migration_head
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_migration_readiness_accepts_current_and_rejects_stale_database(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    expected = expected_migration_head()
    assert expected == "20260723_0006"
    try:
        await verify_database_migration_head(engine)
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE alembic_version SET version_num = '20260722_0005'")
            )
        with pytest.raises(RuntimeError, match="migration is not current"):
            await verify_database_migration_head(engine)
    finally:
        async with engine.begin() as connection:
            await connection.execute(
                text("UPDATE alembic_version SET version_num = :version"),
                {"version": expected},
            )
        await engine.dispose()

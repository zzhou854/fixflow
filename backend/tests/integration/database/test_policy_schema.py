"""PostgreSQL policy constraints and fixed vector dimension."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest


def _vector(dimension: int = 384) -> str:
    return "[" + ",".join("0" for _ in range(dimension)) + "]"


async def _document(
    connection: asyncpg.Connection,
    *,
    code: str | None = None,
    version: int = 1,
    start: datetime | None = None,
    end: datetime | None = None,
    enabled: bool = True,
) -> UUID:
    document_id = uuid4()
    await connection.execute(
        "INSERT INTO policy_documents "
        "(id, policy_code, title, issue_category, policy_topic, version, authority_rank, "
        "effective_from, effective_to, source_name, source_reference, content_hash, "
        "embedding_provider, embedding_model, embedding_dimension, embedding_profile_version, "
        "is_enabled) "
        "VALUES ($1, $2, 'Synthetic test', 'WATER_LEAK', 'SAFETY_ESCALATION', $3, 50, "
        "$4, $5, 'FixFlow synthetic policy corpus', 'demo://schema-test', $6, "
        "'fixflow-test', 'character-bigram-hash', 384, 'v1', $7)",
        document_id,
        code or f"TEST_{document_id.hex.upper()}",
        version,
        start or datetime(2030, 1, 1, tzinfo=UTC),
        end,
        document_id.hex * 2,
        enabled,
    )
    return document_id


@pytest.mark.asyncio
async def test_valid_policy_document_and_chunk_can_be_written(
    db_connection: asyncpg.Connection,
) -> None:
    document_id = await _document(db_connection)
    await db_connection.execute(
        "INSERT INTO policy_chunks "
        "(id, document_id, chunk_index, content, embedding, search_terms, "
        "decision_key, decision_value) VALUES ($1, $2, 0, 'synthetic content', "
        "$3::vector, ARRAY['term'], 'route', 'manual')",
        uuid4(),
        document_id,
        _vector(),
    )


@pytest.mark.asyncio
async def test_invalid_effective_period_is_rejected(db_connection: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _document(
            db_connection,
            start=datetime(2030, 1, 2, tzinfo=UTC),
            end=datetime(2030, 1, 1, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_policy_code_version_is_unique(db_connection: asyncpg.Connection) -> None:
    code = f"UNIQUE_{uuid4().hex.upper()}"
    await _document(db_connection, code=code)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _document(db_connection, code=code)


@pytest.mark.asyncio
async def test_same_policy_code_effective_period_cannot_overlap(
    db_connection: asyncpg.Connection,
) -> None:
    code = f"OVERLAP_{uuid4().hex.upper()}"
    await _document(
        db_connection,
        code=code,
        version=1,
        start=datetime(2030, 1, 1, tzinfo=UTC),
        end=datetime(2031, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(asyncpg.ExclusionViolationError):
        await _document(
            db_connection,
            code=code,
            version=2,
            start=datetime(2030, 6, 1, tzinfo=UTC),
            end=datetime(2032, 1, 1, tzinfo=UTC),
        )


@pytest.mark.asyncio
async def test_adjacent_policy_periods_are_allowed(db_connection: asyncpg.Connection) -> None:
    code = f"ADJACENT_{uuid4().hex.upper()}"
    boundary = datetime(2031, 1, 1, tzinfo=UTC)
    await _document(
        db_connection,
        code=code,
        version=1,
        start=datetime(2030, 1, 1, tzinfo=UTC),
        end=boundary,
    )
    await _document(db_connection, code=code, version=2, start=boundary)


@pytest.mark.asyncio
async def test_open_ended_period_blocks_later_same_code(
    db_connection: asyncpg.Connection,
) -> None:
    code = f"OPEN_END_{uuid4().hex.upper()}"
    await _document(db_connection, code=code, version=1, start=datetime(2030, 1, 1, tzinfo=UTC))
    with pytest.raises(asyncpg.ExclusionViolationError) as caught:
        await _document(db_connection, code=code, version=2, start=datetime(2031, 1, 1, tzinfo=UTC))
    assert caught.value.sqlstate == "23P01"
    assert caught.value.constraint_name == "ex_policy_documents_code_effective_overlap"


@pytest.mark.asyncio
async def test_different_policy_codes_may_have_overlapping_periods(
    db_connection: asyncpg.Connection,
) -> None:
    await _document(db_connection, code=f"CODE_A_{uuid4().hex.upper()}")
    await _document(db_connection, code=f"CODE_B_{uuid4().hex.upper()}")


@pytest.mark.asyncio
async def test_disabled_document_still_preserves_period_integrity(
    db_connection: asyncpg.Connection,
) -> None:
    code = f"DISABLED_{uuid4().hex.upper()}"
    await _document(db_connection, code=code, version=1, enabled=False)
    with pytest.raises(asyncpg.ExclusionViolationError):
        await _document(db_connection, code=code, version=2)


@pytest.mark.asyncio
async def test_embedding_profile_dimension_must_match_vector_schema(
    db_connection: asyncpg.Connection,
) -> None:
    document_id = uuid4()
    with pytest.raises(asyncpg.CheckViolationError):
        await db_connection.execute(
            "INSERT INTO policy_documents "
            "(id, policy_code, title, issue_category, policy_topic, version, authority_rank, "
            "effective_from, source_name, source_reference, content_hash, embedding_provider, "
            "embedding_model, embedding_dimension, embedding_profile_version, is_enabled) "
            "VALUES ($1, $2, 'Synthetic', 'WATER_LEAK', 'SAFETY_ESCALATION', 1, 50, $3, "
            "'synthetic', 'demo://profile', $4, 'fixflow-test', 'model', 12, 'v1', true)",
            document_id,
            f"PROFILE_{document_id.hex.upper()}",
            datetime(2030, 1, 1, tzinfo=UTC),
            document_id.hex * 2,
        )


@pytest.mark.asyncio
async def test_concurrent_overlapping_versions_allow_only_one_commit(
    migrated_database_url: str,
) -> None:
    import asyncio

    url = migrated_database_url.replace("postgresql+asyncpg://", "postgresql://")
    first = await asyncpg.connect(url)
    second = await asyncpg.connect(url)
    code = f"CONCURRENT_{uuid4().hex.upper()}"
    first_tx = first.transaction()
    await first_tx.start()
    await _document(first, code=code, version=1)

    async def insert_second() -> str:
        async with second.transaction():
            await _document(second, code=code, version=2)
        return "committed"

    second_task = asyncio.create_task(insert_second())
    await asyncio.sleep(0.05)
    await first_tx.commit()
    try:
        with pytest.raises(asyncpg.ExclusionViolationError) as caught:
            await second_task
        assert caught.value.sqlstate == "23P01"
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_chunk_index_is_unique_and_dimension_is_fixed(
    db_connection: asyncpg.Connection,
) -> None:
    document_id = await _document(db_connection)
    await db_connection.execute(
        "INSERT INTO policy_chunks "
        "(id, document_id, chunk_index, content, embedding, search_terms) "
        "VALUES ($1, $2, 0, 'first', $3::vector, ARRAY['term'])",
        uuid4(),
        document_id,
        _vector(),
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await db_connection.execute(
            "INSERT INTO policy_chunks "
            "(id, document_id, chunk_index, content, embedding, search_terms) "
            "VALUES ($1, $2, 0, 'duplicate', $3::vector, ARRAY['term'])",
            uuid4(),
            document_id,
            _vector(),
        )


@pytest.mark.asyncio
async def test_wrong_embedding_dimension_is_rejected(db_connection: asyncpg.Connection) -> None:
    document_id = await _document(db_connection)
    with pytest.raises(asyncpg.DataError):
        await db_connection.execute(
            "INSERT INTO policy_chunks "
            "(id, document_id, chunk_index, content, embedding, search_terms) "
            "VALUES ($1, $2, 0, 'wrong dimension', $3::vector, ARRAY['term'])",
            uuid4(),
            document_id,
            _vector(3),
        )


@pytest.mark.asyncio
async def test_partial_decision_pair_is_rejected(db_connection: asyncpg.Connection) -> None:
    document_id = await _document(db_connection)
    with pytest.raises(asyncpg.CheckViolationError):
        await db_connection.execute(
            "INSERT INTO policy_chunks "
            "(id, document_id, chunk_index, content, embedding, search_terms, decision_key) "
            "VALUES ($1, $2, 0, 'partial decision', $3::vector, ARRAY['term'], 'route')",
            uuid4(),
            document_id,
            _vector(),
        )

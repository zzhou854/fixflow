"""Atomic policy import, replay, conflict, and provider-failure tests."""

from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from app.infrastructure.database.models.policy import PolicyChunk, PolicyDocument
from app.infrastructure.database.policy_uow import SqlAlchemyPolicyUnitOfWork
from app.policy.corpus import load_policy_corpus
from app.policy.errors import (
    EmbeddingDimensionMismatch,
    EmbeddingProfileConflict,
    EmbeddingProviderError,
    PolicyEffectivePeriodConflict,
    PolicyImportConflict,
)
from app.policy.import_service import PolicyImportService, policy_document_id
from app.policy.models import EmbeddingProfile
from app.policy.ports import PolicyUnitOfWorkFactory
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tests.fakes.embedding import DeterministicEmbeddingProvider


@pytest.mark.asyncio
async def test_same_document_replay_is_idempotent(policy_services: tuple[object, ...]) -> None:
    importer = policy_services[0]
    assert isinstance(importer, PolicyImportService)
    document = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json")).documents[0]
    first = await importer.import_document(document)
    second = await importer.import_document(document)
    assert first.document_id == second.document_id
    assert first.replayed is True
    assert second.replayed is True


@pytest.mark.asyncio
async def test_embedding_profile_is_persisted(policy_services: tuple[object, ...]) -> None:
    importer, _, uow_factory, provider, _ = policy_services
    assert isinstance(importer, PolicyImportService)
    assert callable(uow_factory)
    assert isinstance(provider, DeterministicEmbeddingProvider)
    document = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json")).documents[0]
    imported = await importer.import_document(document)
    async with uow_factory() as uow:
        identity = await uow.policies.get_identity(document.policy_code, document.version)
    assert identity is not None
    assert identity.document_id == imported.document_id
    assert identity.embedding_profile == provider.profile


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model", "profile_version"),
    [
        ("other-provider", "character-bigram-hash", "v1"),
        ("fixflow-test", "other-model", "v1"),
        ("fixflow-test", "character-bigram-hash", "v2"),
    ],
)
async def test_same_content_with_different_embedding_profile_is_conflict(
    policy_services: tuple[object, ...],
    provider: str,
    model: str,
    profile_version: str,
) -> None:
    uow_factory = policy_services[2]
    document = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json")).documents[0]
    service = PolicyImportService(
        uow_factory=cast(PolicyUnitOfWorkFactory, uow_factory),
        embedding_provider=DeterministicEmbeddingProvider(
            provider=provider, model=model, profile_version=profile_version
        ),
    )
    with pytest.raises(EmbeddingProfileConflict):
        await service.import_document(document)


@pytest.mark.asyncio
async def test_document_and_evidence_ids_are_stable_across_fresh_databases(
    two_migrated_database_urls: tuple[str, str],
) -> None:
    document = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json")).documents[0]
    identities: list[tuple[object, tuple[object, ...]]] = []
    for database_url in two_migrated_database_urls:
        engine = create_async_engine(database_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        service = PolicyImportService(
            uow_factory=lambda factory=factory: SqlAlchemyPolicyUnitOfWork(factory),
            embedding_provider=DeterministicEmbeddingProvider(),
        )
        imported = await service.import_document(document)
        async with factory() as session:
            chunk_ids = tuple(
                (
                    await session.scalars(
                        select(PolicyChunk.id)
                        .where(PolicyChunk.document_id == imported.document_id)
                        .order_by(PolicyChunk.chunk_index)
                    )
                ).all()
            )
        identities.append((imported.document_id, chunk_ids))
        await engine.dispose()
    assert identities[0] == identities[1]


@pytest.mark.asyncio
async def test_same_version_different_content_is_conflict(
    policy_services: tuple[object, ...],
) -> None:
    importer = policy_services[0]
    assert isinstance(importer, PolicyImportService)
    document = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json")).documents[0]
    changed_chunk = document.chunks[0].model_copy(update={"content": "冲突内容"})
    changed = document.model_copy(update={"chunks": (changed_chunk,)})
    with pytest.raises(PolicyImportConflict):
        await importer.import_document(changed)


@pytest.mark.asyncio
async def test_embedding_failure_creates_no_document(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def uow_factory() -> SqlAlchemyPolicyUnitOfWork:
        return SqlAlchemyPolicyUnitOfWork(factory)

    provider = DeterministicEmbeddingProvider(failures=[TimeoutError("offline")])
    service = PolicyImportService(uow_factory=uow_factory, embedding_provider=provider)
    original = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json")).documents[0]
    code = f"FAIL_{uuid4().hex.upper()}"
    document = original.model_copy(update={"policy_code": code})
    with pytest.raises(EmbeddingProviderError):
        await service.import_document(document)
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(PolicyDocument)
            .where(PolicyDocument.policy_code == code)
        )
    assert count == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_provider_dimension_mismatch_fails_before_database_write(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)

    def uow_factory() -> SqlAlchemyPolicyUnitOfWork:
        return SqlAlchemyPolicyUnitOfWork(factory)

    service = PolicyImportService(
        uow_factory=uow_factory,
        embedding_provider=DeterministicEmbeddingProvider(dimension=12),
    )
    original = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json")).documents[0]
    with pytest.raises(EmbeddingDimensionMismatch):
        await service.import_document(
            original.model_copy(update={"policy_code": f"DIM_{uuid4().hex.upper()}"})
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_chunk_constraint_failure_rolls_back_document(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(engine, expire_on_commit=False)

    def uow_factory() -> SqlAlchemyPolicyUnitOfWork:
        return SqlAlchemyPolicyUnitOfWork(factory)

    original = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json")).documents[0]
    code = f"ROLLBACK_{uuid4().hex.upper()}"
    document = original.model_copy(update={"policy_code": code})
    with pytest.raises(IntegrityError):
        async with uow_factory() as uow:
            profile = EmbeddingProfile(
                provider="fixflow-test",
                model="character-bigram-hash",
                dimension=384,
                profile_version="v1",
            )
            document_id = policy_document_id(document, "b" * 64)
            await uow.policies.add_document(document_id, document, "b" * 64, profile)
            await uow.policies.add_chunks(
                document_id,
                [(uuid4(), 0, "content", (), (0.0,) * 384, None, None)],
            )
            await uow.flush()
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(PolicyDocument)
            .where(PolicyDocument.policy_code == code)
        )
    assert count == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_effective_period_conflict_is_mapped_and_transaction_rolls_back(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = PolicyImportService(
        uow_factory=lambda: SqlAlchemyPolicyUnitOfWork(factory),
        embedding_provider=DeterministicEmbeddingProvider(),
    )
    original = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json")).documents[0]
    code = f"PERIOD_{uuid4().hex.upper()}"
    first = original.model_copy(update={"policy_code": code, "version": 1})
    second = original.model_copy(update={"policy_code": code, "version": 2})
    await service.import_document(first)
    with pytest.raises(PolicyEffectivePeriodConflict, match="SQLSTATE 23P01"):
        await service.import_document(second)
    async with factory() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(PolicyDocument)
            .where(PolicyDocument.policy_code == code)
        )
    assert count == 1
    await engine.dispose()

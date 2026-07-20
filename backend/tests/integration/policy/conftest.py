"""One imported synthetic corpus for real PostgreSQL policy tests."""

from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest_asyncio
from app.infrastructure.database.policy_uow import SqlAlchemyPolicyUnitOfWork
from app.policy.corpus import load_policy_corpus
from app.policy.import_service import PolicyImportService
from app.policy.retrieval import PolicyRetrievalService
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from tests.fakes.embedding import DeterministicEmbeddingProvider


@pytest_asyncio.fixture
async def policy_services(
    migrated_database_url: str,
) -> AsyncIterator[
    tuple[
        PolicyImportService,
        PolicyRetrievalService,
        Callable[[], SqlAlchemyPolicyUnitOfWork],
        DeterministicEmbeddingProvider,
        AsyncEngine,
    ]
]:
    engine = create_async_engine(migrated_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    def uow_factory() -> SqlAlchemyPolicyUnitOfWork:
        return SqlAlchemyPolicyUnitOfWork(session_factory)

    provider = DeterministicEmbeddingProvider()
    importer = PolicyImportService(uow_factory=uow_factory, embedding_provider=provider)
    retriever = PolicyRetrievalService(uow_factory=uow_factory, embedding_provider=provider)
    corpus = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json"))
    for document in corpus.documents:
        await importer.import_document(document)
    yield importer, retriever, uow_factory, provider, engine
    await engine.dispose()

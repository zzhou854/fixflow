"""Idempotent, non-production demonstration data seed."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql.ranges import Range
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.demo_providers import DemoDeterministicEmbeddingProvider
from app.config import get_settings
from app.domain.enums import WorkerSkillType
from app.infrastructure.database.models import (
    Property,
    ResidentPropertyRelation,
    User,
    Worker,
    WorkerAvailability,
    WorkerSkill,
)
from app.infrastructure.database.policy_uow import SqlAlchemyPolicyUnitOfWork
from app.policy.corpus import load_policy_corpus
from app.policy.import_service import PolicyImportService

DEMO_RESIDENT_USERNAME = "resident_demo"
DEMO_OPERATOR_USERNAME = "operator_demo"
DEMO_RESIDENT_PASSWORD = "ResidentDemo!2026"
DEMO_OPERATOR_PASSWORD = "OperatorDemo!2026"


def _id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"fixflow:demo-seed:{name}")


async def seed() -> None:
    settings = get_settings()
    engine = create_async_engine(settings.database_url.get_secret_value())
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    hasher = PasswordHasher()
    async with sessions() as session:
        resident = await _upsert_user(
            session,
            user_id=_id("resident"),
            username=DEMO_RESIDENT_USERNAME,
            password=DEMO_RESIDENT_PASSWORD,
            role="RESIDENT",
            hasher=hasher,
        )
        await _upsert_user(
            session,
            user_id=_id("operator"),
            username=DEMO_OPERATOR_USERNAME,
            password=DEMO_OPERATOR_PASSWORD,
            role="OPERATOR",
            hasher=hasher,
        )
        property_ = await session.get(Property, _id("property"))
        if property_ is None:
            property_ = Property(
                id=_id("property"),
                community_name="星河花园",
                building_no="3",
                unit_no="2",
                room_no="1201",
                address_text="星河花园 3 栋 2 单元 1201",
                is_active=True,
            )
            session.add(property_)
        relation = await session.scalar(
            select(ResidentPropertyRelation).where(
                ResidentPropertyRelation.resident_id == resident.id,
                ResidentPropertyRelation.property_id == property_.id,
            )
        )
        if relation is None:
            session.add(
                ResidentPropertyRelation(
                    id=_id("resident-property"),
                    resident_id=resident.id,
                    property_id=property_.id,
                    is_active=True,
                )
            )
        for skill, suffix, name in (
            (WorkerSkillType.PLUMBING, "plumbing", "演示水暖维修员"),
            (WorkerSkillType.ELECTRICAL, "electrical", "演示电工"),
            (WorkerSkillType.LOCKSMITH, "locksmith", "演示锁匠"),
        ):
            worker_id = _id(f"worker-{suffix}")
            worker = await session.get(Worker, worker_id)
            if worker is None:
                session.add(
                    Worker(
                        id=worker_id,
                        name=name,
                        service_area="星河花园",
                        is_active=True,
                    )
                )
                session.add(WorkerSkill(worker_id=worker_id, skill_type=skill))
                session.add(
                    WorkerAvailability(
                        id=_id(f"availability-{suffix}"),
                        worker_id=worker_id,
                        available_range=Range(
                            datetime(2026, 1, 1, tzinfo=UTC),
                            datetime(2028, 1, 1, tzinfo=UTC),
                            bounds="[)",
                        ),
                    )
                )
        await session.commit()

    def policy_uow_factory() -> SqlAlchemyPolicyUnitOfWork:
        return SqlAlchemyPolicyUnitOfWork(sessions)

    importer = PolicyImportService(
        uow_factory=policy_uow_factory,
        embedding_provider=DemoDeterministicEmbeddingProvider(),
    )
    corpus = load_policy_corpus(Path("data/policies/fixflow_demo_policies.json"))
    for document in corpus.documents:
        await importer.import_document(document)
    await engine.dispose()


async def _upsert_user(
    session: object,
    *,
    user_id: UUID,
    username: str,
    password: str,
    role: str,
    hasher: PasswordHasher,
) -> User:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    user = await session.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            username=username,
            password_hash=hasher.hash(password),
            role=role,
            is_active=True,
        )
        session.add(user)
        return user
    try:
        matches: bool = hasher.verify(user.password_hash, password)
    except VerifyMismatchError:
        matches = False
    if not matches:
        user.password_hash = hasher.hash(password)
    user.is_active = True
    return user


if __name__ == "__main__":
    asyncio.run(seed())

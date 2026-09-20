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
TEST_RESIDENT_USERNAME = "resident_test"
TEST_RESIDENT_PASSWORD = "ResidentTest!2026"
TEST_OPERATOR_USERNAME = "operator_test"
TEST_OPERATOR_PASSWORD = "OperatorTest!2026"


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
        test_resident = await _upsert_user(
            session,
            user_id=_id("resident-test"),
            username=TEST_RESIDENT_USERNAME,
            password=TEST_RESIDENT_PASSWORD,
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
        await _upsert_user(
            session,
            user_id=_id("operator-test"),
            username=TEST_OPERATOR_USERNAME,
            password=TEST_OPERATOR_PASSWORD,
            role="OPERATOR",
            hasher=hasher,
        )
        existing_properties = {
            (item.building_no, item.unit_no, item.room_no): item
            for item in (
                await session.scalars(select(Property).where(Property.community_name == "星河花园"))
            ).all()
        }
        properties: dict[tuple[str, str, str], Property] = {}
        for building in range(1, 4):
            for unit in range(1, 3):
                for floor in range(1, 13):
                    for door in range(1, 5):
                        key = (str(building), str(unit), f"{floor}{door:02d}")
                        property_ = existing_properties.get(key)
                        if property_ is None:
                            property_ = Property(
                                id=(
                                    _id("property")
                                    if key == ("3", "2", "1201")
                                    else _id(f"property-{'-'.join(key)}")
                                ),
                                community_name="星河花园",
                                building_no=key[0],
                                unit_no=key[1],
                                room_no=key[2],
                                address_text=(f"星河花园 {key[0]} 栋 {key[1]} 单元 {key[2]}"),
                                is_active=True,
                            )
                            session.add(property_)
                        properties[key] = property_
        for account, relation_name, property_key in (
            (resident, "resident-property", ("3", "2", "1201")),
            (test_resident, "resident-test-property", ("1", "1", "101")),
        ):
            relation = await session.get(ResidentPropertyRelation, _id(relation_name))
            if relation is None:
                session.add(
                    ResidentPropertyRelation(
                        id=_id(relation_name),
                        resident_id=account.id,
                        property_id=properties[property_key].id,
                        is_active=True,
                    )
                )
            else:
                relation.property_id = properties[property_key].id
                relation.is_active = True
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

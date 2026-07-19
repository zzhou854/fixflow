"""Real PostgreSQL environment for MCP adapter and transport tests."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest_asyncio
from app.application.ports import UnitOfWork
from app.application.services import FixFlowApplicationService
from app.domain.enums import WorkerSkillType
from app.infrastructure.database.models import (
    Property,
    ResidentPropertyRelation,
    User,
    Worker,
    WorkerAvailability,
    WorkerSkill,
)
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from sqlalchemy.dialects.postgresql.ranges import Range
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mcp_server.application_adapter import MCPApplicationAdapter


@dataclass(slots=True)
class MCPIntegrationEnvironment:
    adapter: MCPApplicationAdapter
    application: FixFlowApplicationService
    sessions: async_sessionmaker[AsyncSession]
    database_url: str
    resident_id: UUID
    other_resident_id: UUID
    operator_id: UUID
    property_id: UUID
    worker_ids: tuple[UUID, UUID]
    wrong_skill_worker_id: UUID
    inactive_worker_id: UUID
    wrong_area_worker_id: UUID
    slot: datetime
    community_name: str


@pytest_asyncio.fixture
async def mcp_env(migrated_database_url: str) -> AsyncIterator[MCPIntegrationEnvironment]:
    engine = create_async_engine(migrated_database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    resident_id = uuid4()
    other_resident_id = uuid4()
    operator_id = uuid4()
    property_id = uuid4()
    worker_ids = (uuid4(), uuid4())
    wrong_skill_worker_id = uuid4()
    inactive_worker_id = uuid4()
    wrong_area_worker_id = uuid4()
    slot = datetime(2031, 2, 3, 9, tzinfo=UTC)
    community_name = f"Green Garden {property_id}"
    async with sessions.begin() as session:
        session.add_all(
            [
                User(
                    id=resident_id,
                    username=f"mcp-resident-{resident_id}",
                    password_hash="not-plaintext",
                    role="RESIDENT",
                ),
                User(
                    id=other_resident_id,
                    username=f"mcp-other-{other_resident_id}",
                    password_hash="not-plaintext",
                    role="RESIDENT",
                ),
                User(
                    id=operator_id,
                    username=f"mcp-operator-{operator_id}",
                    password_hash="not-plaintext",
                    role="OPERATOR",
                ),
                Property(
                    id=property_id,
                    community_name=community_name,
                    building_no="2",
                    unit_no="1",
                    room_no=property_id.hex[:8],
                    address_text=f"{community_name} 2-1-{property_id.hex[:8]}",
                ),
                ResidentPropertyRelation(resident_id=resident_id, property_id=property_id),
            ]
        )
        worker_specs = (
            (worker_ids[0], WorkerSkillType.PLUMBING, community_name, True),
            (worker_ids[1], WorkerSkillType.PLUMBING, f"  {community_name.upper()}  ", True),
            (wrong_skill_worker_id, WorkerSkillType.ELECTRICAL, community_name, True),
            (inactive_worker_id, WorkerSkillType.PLUMBING, community_name, False),
            (wrong_area_worker_id, WorkerSkillType.PLUMBING, "Other Community", True),
        )
        for index, (worker_id, skill, area, active) in enumerate(worker_specs):
            session.add(
                Worker(
                    id=worker_id,
                    name=f"mcp-worker-{index}",
                    service_area=area,
                    is_active=active,
                )
            )
            session.add(WorkerSkill(worker_id=worker_id, skill_type=skill))
            session.add(
                WorkerAvailability(
                    worker_id=worker_id,
                    available_range=Range(
                        slot - timedelta(hours=1), slot + timedelta(days=5), bounds="[)"
                    ),
                )
            )

    def uow_factory() -> UnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    application = FixFlowApplicationService(uow_factory)
    environment = MCPIntegrationEnvironment(
        adapter=MCPApplicationAdapter(application, clock=lambda: slot),
        application=application,
        sessions=sessions,
        database_url=migrated_database_url,
        resident_id=resident_id,
        other_resident_id=other_resident_id,
        operator_id=operator_id,
        property_id=property_id,
        worker_ids=worker_ids,
        wrong_skill_worker_id=wrong_skill_worker_id,
        inactive_worker_id=inactive_worker_id,
        wrong_area_worker_id=wrong_area_worker_id,
        slot=slot,
        community_name=community_name,
    )
    try:
        yield environment
    finally:
        await engine.dispose()

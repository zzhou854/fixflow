"""SQLAlchemy metadata and PostgreSQL type mapping tests."""

from uuid import uuid4

import app.infrastructure.database.models  # noqa: F401
import pytest
from app.domain.enums import AppointmentStatus, TicketStatus
from app.infrastructure.database.base import Base
from app.infrastructure.database.models import Property, ResidentPropertyRelation, User
from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Enum, select
from sqlalchemy.dialects.postgresql import TSTZRANGE
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload


def test_metadata_contains_exact_core_tables() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "properties",
        "resident_property_relations",
        "repair_tickets",
        "ticket_status_history",
        "workers",
        "worker_skills",
        "worker_availability",
        "appointments",
        "appointment_status_history",
        "worker_events",
        "idempotency_records",
        "policy_documents",
        "policy_chunks",
        "agent_runs",
        "agent_trace_events",
        "outbox_events",
        "operation_reconciliation_cases",
        "agent_replay_bundles",
        "agent_replay_steps",
        "agent_replay_executions",
    }


def test_status_columns_are_non_native_varchar_enums() -> None:
    ticket_type = Base.metadata.tables["repair_tickets"].c.status.type
    appointment_type = Base.metadata.tables["appointments"].c.status.type
    assert isinstance(ticket_type, Enum)
    assert isinstance(appointment_type, Enum)
    assert ticket_type.native_enum is False
    assert appointment_type.native_enum is False
    assert ticket_type.enums == [item.value for item in TicketStatus]
    assert appointment_type.enums == [item.value for item in AppointmentStatus]


def test_business_times_are_timezone_aware_and_ranges_are_tstzrange() -> None:
    tickets = Base.metadata.tables["repair_tickets"]
    appointments = Base.metadata.tables["appointments"]
    assert isinstance(tickets.c.created_at.type, DateTime)
    assert tickets.c.created_at.type.timezone is True
    assert isinstance(appointments.c.scheduled_range.type, TSTZRANGE)
    policy_documents = Base.metadata.tables["policy_documents"]
    policy_chunks = Base.metadata.tables["policy_chunks"]
    assert isinstance(policy_documents.c.effective_from.type, DateTime)
    assert policy_documents.c.effective_from.type.timezone is True
    assert isinstance(policy_chunks.c.embedding.type, Vector)
    assert policy_chunks.c.embedding.type.dim == 384


def test_orm_models_define_no_domain_transition_methods() -> None:
    forbidden = {"transition", "close", "cancel", "reschedule", "record_event"}
    for mapper in Base.registry.mappers:
        assert forbidden.isdisjoint(vars(mapper.class_))


@pytest.mark.asyncio
async def test_key_relationship_can_be_persisted_and_loaded(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    resident_id = uuid4()
    property_id = uuid4()
    async with session_factory() as session:
        async with session.begin():
            session.add_all(
                [
                    User(
                        id=resident_id,
                        username=f"orm-{resident_id}",
                        password_hash="not-plaintext",
                        role="RESIDENT",
                    ),
                    Property(
                        id=property_id,
                        community_name="ORM Garden",
                        building_no="1",
                        unit_no="1",
                        room_no=property_id.hex[:8],
                        address_text="test address",
                    ),
                    ResidentPropertyRelation(
                        resident_id=resident_id,
                        property_id=property_id,
                    ),
                ]
            )
        loaded = await session.scalar(
            select(User)
            .where(User.id == resident_id)
            .options(selectinload(User.resident_properties))
        )
        assert loaded is not None
        assert loaded.created_at.tzinfo is not None
        assert [item.property_id for item in loaded.resident_properties] == [property_id]
    await engine.dispose()

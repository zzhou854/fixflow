"""SQLAlchemy adapter for authentication identities and resident registration."""

from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.auth import AuthUser, RegistrationError
from app.domain.enums import ActorType
from app.infrastructure.database.models import Property, ResidentPropertyRelation, User


class SqlAlchemyAuthUserRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def find_by_username(self, username: str) -> AuthUser | None:
        async with self._sessions() as session:
            row = await session.scalar(select(User).where(User.username == username))
            return self._map(row)

    async def find_by_id(self, user_id: UUID) -> AuthUser | None:
        async with self._sessions() as session:
            row = await session.get(User, user_id)
            return self._map(row)

    async def register_resident(
        self,
        *,
        username: str,
        password_hash: str,
        community_name: str,
        building_no: str,
        unit_no: str,
        room_no: str,
    ) -> AuthUser:
        try:
            async with self._sessions.begin() as session:
                if await session.scalar(select(User.id).where(User.username == username)):
                    raise RegistrationError("USERNAME_TAKEN")
                property_ = await session.scalar(
                    select(Property).where(
                        Property.community_name == community_name,
                        Property.building_no == building_no,
                        Property.unit_no == unit_no,
                        Property.room_no == room_no,
                        Property.is_active.is_(True),
                    )
                )
                if property_ is None:
                    raise RegistrationError("PROPERTY_NOT_FOUND")
                row = User(
                    id=uuid4(),
                    username=username,
                    password_hash=password_hash,
                    role=ActorType.RESIDENT.value,
                    is_active=True,
                )
                session.add(row)
                session.add(
                    ResidentPropertyRelation(
                        id=uuid4(),
                        resident_id=row.id,
                        property_id=property_.id,
                        is_active=True,
                    )
                )
                await session.flush()
                mapped = self._map(row)
                assert mapped is not None
                return mapped
        except IntegrityError as exc:
            raise RegistrationError("USERNAME_TAKEN") from exc

    @staticmethod
    def _map(row: User | None) -> AuthUser | None:
        if row is None:
            return None
        return AuthUser(
            user_id=row.id,
            username=row.username,
            actor_type=ActorType(row.role),
            is_active=row.is_active,
            password_hash=row.password_hash,
        )

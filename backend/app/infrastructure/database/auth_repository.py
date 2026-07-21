"""SQLAlchemy adapter for preset authentication identities."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.auth import AuthUser
from app.domain.enums import ActorType
from app.infrastructure.database.models import User


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

"""Focused SQLAlchemy UoW for Agent-control persistence."""

from types import TracebackType

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.agent_reliability import AgentReliabilityConflict
from app.application.agent_reliability_ports import AgentReliabilityRepository
from app.infrastructure.database.repositories.agent_reliability import (
    SqlAlchemyAgentReliabilityRepository,
)


class SqlAlchemyAgentReliabilityUnitOfWork:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def __aenter__(self) -> "SqlAlchemyAgentReliabilityUnitOfWork":
        self.session = self._sessions()
        self._committed = False
        self.reliability: AgentReliabilityRepository = SqlAlchemyAgentReliabilityRepository(
            self.session
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if not self._committed:
            await self.session.rollback()
        await self.session.close()

    async def commit(self) -> None:
        try:
            await self.session.commit()
            self._committed = True
        except IntegrityError as exc:
            raise AgentReliabilityConflict("agent reliability persistence conflict") from exc

    async def rollback(self) -> None:
        await self.session.rollback()

"""Dedicated transaction boundary for policy import and retrieval."""

from types import TracebackType

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.repositories.policy import SqlAlchemyPolicyRepository
from app.policy.errors import PolicyEffectivePeriodConflict
from app.policy.ports import PolicyRepository

_EFFECTIVE_PERIOD_CONSTRAINT = "ex_policy_documents_code_effective_overlap"


def _translate_policy_integrity_error(error: IntegrityError) -> Exception:
    original = error.orig
    candidates = (original, getattr(original, "__cause__", None))
    sqlstate = next(
        (
            getattr(item, "sqlstate", None)
            for item in candidates
            if item is not None
            if getattr(item, "sqlstate", None)
        ),
        None,
    )
    constraint = next(
        (
            getattr(item, "constraint_name", None)
            for item in candidates
            if item is not None
            if getattr(item, "constraint_name", None)
        ),
        None,
    )
    if sqlstate == "23P01" and constraint == _EFFECTIVE_PERIOD_CONSTRAINT:
        return PolicyEffectivePeriodConflict(
            f"policy effective-period conflict ({constraint}, SQLSTATE {sqlstate})"
        )
    return error


class SqlAlchemyPolicyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __aenter__(self) -> "SqlAlchemyPolicyUnitOfWork":
        self.session = self._session_factory()
        self._committed = False
        self.policies: PolicyRepository = SqlAlchemyPolicyRepository(self.session)
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

    async def flush(self) -> None:
        try:
            await self.session.flush()
        except IntegrityError as exc:
            translated = _translate_policy_integrity_error(exc)
            if translated is exc:
                raise
            raise translated from exc

    async def commit(self) -> None:
        try:
            await self.session.commit()
            self._committed = True
        except IntegrityError as exc:
            translated = _translate_policy_integrity_error(exc)
            if translated is exc:
                raise
            raise translated from exc

    async def rollback(self) -> None:
        await self.session.rollback()

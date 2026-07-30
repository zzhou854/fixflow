"""Ports for the focused Agent reliability transaction boundary."""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from typing import Protocol
from uuid import UUID

from app.application.agent_reliability_models import (
    DurableMessage,
    FinalizeAgentRun,
    HumanReviewCaseRecord,
    HumanReviewEventRecord,
    HumanReviewStatus,
    HumanReviewTransition,
    PublicRunResult,
    ThreadLifecycleStatus,
    ThreadRecord,
)


class AgentReliabilityRepository(Protocol):
    async def register_thread(
        self,
        *,
        thread_id: UUID,
        resident_id: UUID,
        property_id: UUID | None,
    ) -> ThreadRecord: ...

    async def finalize_run(self, command: FinalizeAgentRun) -> HumanReviewCaseRecord | None: ...

    async def list_messages(self, thread_id: UUID) -> Sequence[DurableMessage]: ...
    async def latest_public_result(self, thread_id: UUID) -> PublicRunResult | None: ...

    async def list_threads(
        self,
        resident_id: UUID,
        *,
        lifecycle_status: ThreadLifecycleStatus | None,
        limit: int,
        offset: int,
    ) -> Sequence[ThreadRecord]: ...

    async def get_thread(
        self, thread_id: UUID, *, for_update: bool = False
    ) -> ThreadRecord | None: ...

    async def set_thread_lifecycle(
        self,
        *,
        thread_id: UUID,
        resident_id: UUID,
        lifecycle_status: ThreadLifecycleStatus,
        actor_id: UUID,
        expected_version: int,
    ) -> ThreadRecord: ...

    async def list_human_review_cases(
        self,
        *,
        status: HumanReviewStatus | None,
        limit: int,
        offset: int,
    ) -> Sequence[HumanReviewCaseRecord]: ...

    async def transition_human_review(
        self, command: HumanReviewTransition
    ) -> HumanReviewCaseRecord: ...

    async def list_human_review_events(self, case_id: UUID) -> Sequence[HumanReviewEventRecord]: ...

    async def reconcile_stalled_runs(self, *, older_than_seconds: int, limit: int) -> int: ...


class AgentReliabilityUnitOfWork(Protocol):
    reliability: AgentReliabilityRepository

    async def __aenter__(self) -> AgentReliabilityUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class AgentReliabilityUnitOfWorkFactory(Protocol):
    def __call__(self) -> AgentReliabilityUnitOfWork: ...

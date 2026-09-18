"""Deterministic use cases for Agent results, review tasks, and thread lifecycle."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.application.agent_reliability_models import (
    DurableMessage,
    FinalizeAgentRun,
    HumanReviewCaseRecord,
    HumanReviewEventRecord,
    HumanReviewStatus,
    HumanReviewTransition,
    MessageOutcome,
    PublicRunResult,
    ThreadLifecycleStatus,
    ThreadRecord,
)
from app.application.agent_reliability_ports import AgentReliabilityUnitOfWorkFactory
from app.domain.enums import ActorType


class AgentReliabilityError(Exception):
    code = "AGENT_RELIABILITY_ERROR"


class AgentThreadNotFound(AgentReliabilityError):
    code = "NOT_FOUND"


class AgentThreadPermissionDenied(AgentReliabilityError):
    code = "PERMISSION_DENIED"


class AgentReliabilityConflict(AgentReliabilityError):
    code = "VERSION_CONFLICT"


class HumanReviewPersistenceFailed(AgentReliabilityError):
    code = "HUMAN_REVIEW_PERSISTENCE_FAILED"


class AgentReliabilityService:
    """One narrow application boundary over Agent-control persistence."""

    def __init__(self, uow_factory: AgentReliabilityUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def register_thread(
        self,
        *,
        thread_id: UUID,
        resident_id: UUID,
        property_id: UUID | None,
    ) -> ThreadRecord:
        async with self._uow_factory() as uow:
            record = await uow.reliability.register_thread(
                thread_id=thread_id,
                resident_id=resident_id,
                property_id=property_id,
            )
            await uow.commit()
            return record

    async def finalize_run(self, command: FinalizeAgentRun) -> HumanReviewCaseRecord | None:
        if command.outcome is MessageOutcome.ESCALATED and (
            command.failure_stage is None or command.reason_code is None
        ):
            raise HumanReviewPersistenceFailed(
                "ESCALATED requires a persisted, typed human-review reason"
            )
        try:
            return await self._finalize_once(command)
        except AgentReliabilityConflict:
            if command.outcome is not MessageOutcome.ESCALATED:
                raise
            # A concurrent request may have won the partial unique-key race.
            # Re-run the whole transaction once, preserving this run's terminal
            # result while reusing the already-created review case.
            return await self._finalize_once(command)

    async def _finalize_once(self, command: FinalizeAgentRun) -> HumanReviewCaseRecord | None:
        async with self._uow_factory() as uow:
            review_case = await uow.reliability.finalize_run(command)
            if command.outcome is MessageOutcome.ESCALATED and review_case is None:
                raise HumanReviewPersistenceFailed("human review case was not persisted")
            await uow.commit()
            return review_case

    async def list_messages(
        self, *, thread_id: UUID, resident_id: UUID
    ) -> Sequence[DurableMessage]:
        async with self._uow_factory() as uow:
            thread = await uow.reliability.get_thread(thread_id)
            if thread is None:
                raise AgentThreadNotFound
            if thread.resident_id != resident_id:
                raise AgentThreadPermissionDenied
            if thread.lifecycle_status is ThreadLifecycleStatus.DELETED:
                raise AgentThreadNotFound
            return await uow.reliability.list_messages(thread_id)

    async def require_active_thread(self, *, thread_id: UUID, resident_id: UUID) -> ThreadRecord:
        async with self._uow_factory() as uow:
            thread = await uow.reliability.get_thread(thread_id)
            if thread is None:
                raise AgentThreadNotFound
            if thread.resident_id != resident_id:
                raise AgentThreadPermissionDenied
            if thread.lifecycle_status is not ThreadLifecycleStatus.ACTIVE:
                raise AgentReliabilityConflict("archived thread must be restored first")
            return thread

    async def require_archived_thread(self, *, thread_id: UUID, resident_id: UUID) -> ThreadRecord:
        async with self._uow_factory() as uow:
            thread = await uow.reliability.get_thread(thread_id)
            if thread is None or thread.lifecycle_status is ThreadLifecycleStatus.DELETED:
                raise AgentThreadNotFound
            if thread.resident_id != resident_id:
                raise AgentThreadPermissionDenied
            if thread.lifecycle_status is not ThreadLifecycleStatus.ARCHIVED:
                raise AgentReliabilityConflict("thread must be archived before deletion")
            return thread

    async def latest_public_result(
        self, *, thread_id: UUID, resident_id: UUID
    ) -> PublicRunResult | None:
        async with self._uow_factory() as uow:
            thread = await uow.reliability.get_thread(thread_id)
            if thread is None:
                raise AgentThreadNotFound
            if thread.resident_id != resident_id:
                raise AgentThreadPermissionDenied
            if thread.lifecycle_status is ThreadLifecycleStatus.DELETED:
                raise AgentThreadNotFound
            return await uow.reliability.latest_public_result(thread_id)

    async def list_threads(
        self,
        *,
        resident_id: UUID,
        lifecycle_status: ThreadLifecycleStatus | None,
        limit: int,
        offset: int,
    ) -> Sequence[ThreadRecord]:
        async with self._uow_factory() as uow:
            return await uow.reliability.list_threads(
                resident_id,
                lifecycle_status=lifecycle_status,
                limit=limit,
                offset=offset,
            )

    async def set_thread_lifecycle(
        self,
        *,
        thread_id: UUID,
        resident_id: UUID,
        lifecycle_status: ThreadLifecycleStatus,
        actor_id: UUID,
        expected_version: int,
    ) -> ThreadRecord:
        async with self._uow_factory() as uow:
            current = await uow.reliability.get_thread(thread_id, for_update=True)
            if current is None:
                raise AgentThreadNotFound
            if current.resident_id != resident_id:
                raise AgentThreadPermissionDenied
            if current.version != expected_version:
                raise AgentReliabilityConflict
            allowed = {
                ThreadLifecycleStatus.ACTIVE: {ThreadLifecycleStatus.ARCHIVED},
                ThreadLifecycleStatus.ARCHIVED: {
                    ThreadLifecycleStatus.ACTIVE,
                    ThreadLifecycleStatus.DELETED,
                },
                ThreadLifecycleStatus.DELETED: set(),
            }
            if lifecycle_status not in allowed[current.lifecycle_status]:
                raise AgentReliabilityConflict("invalid thread lifecycle transition")
            updated = await uow.reliability.set_thread_lifecycle(
                thread_id=thread_id,
                resident_id=resident_id,
                lifecycle_status=lifecycle_status,
                actor_id=actor_id,
                expected_version=expected_version,
            )
            await uow.commit()
            return updated

    async def list_human_review_cases(
        self,
        *,
        actor_type: ActorType,
        status: HumanReviewStatus | None,
        limit: int,
        offset: int,
    ) -> Sequence[HumanReviewCaseRecord]:
        if actor_type is not ActorType.OPERATOR:
            raise AgentThreadPermissionDenied
        async with self._uow_factory() as uow:
            return await uow.reliability.list_human_review_cases(
                status=status,
                limit=limit,
                offset=offset,
            )

    async def transition_human_review(
        self, command: HumanReviewTransition
    ) -> HumanReviewCaseRecord:
        if command.actor_type is not ActorType.OPERATOR:
            raise AgentThreadPermissionDenied
        async with self._uow_factory() as uow:
            result = await uow.reliability.transition_human_review(command)
            await uow.commit()
            return result

    async def list_human_review_events(
        self,
        *,
        actor_type: ActorType,
        case_id: UUID,
    ) -> Sequence[HumanReviewEventRecord]:
        if actor_type is not ActorType.OPERATOR:
            raise AgentThreadPermissionDenied
        async with self._uow_factory() as uow:
            return await uow.reliability.list_human_review_events(case_id)

    async def reconcile_stalled_runs(
        self, *, older_than_seconds: int = 120, limit: int = 100
    ) -> int:
        if older_than_seconds <= 0 or not 1 <= limit <= 500:
            raise ValueError("invalid stalled-run reconciliation bounds")
        async with self._uow_factory() as uow:
            count = await uow.reliability.reconcile_stalled_runs(
                older_than_seconds=older_than_seconds,
                limit=limit,
            )
            await uow.commit()
            return count

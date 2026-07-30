"""SQLAlchemy implementation of the focused Agent reliability repository."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.agent_reliability import AgentReliabilityConflict, AgentThreadNotFound
from app.application.agent_reliability_models import (
    AgentMessageRole,
    DurableMessage,
    FinalizeAgentRun,
    HumanReviewCaseRecord,
    HumanReviewEventRecord,
    HumanReviewStatus,
    HumanReviewTransition,
    MessageOutcome,
    PublicRunResult,
    RequiredUserAction,
    ThreadLifecycleStatus,
    ThreadPropertyResolutionStatus,
    ThreadRecord,
)
from app.domain.enums import ActorType
from app.infrastructure.database.models.agent_control import (
    AgentMessageRow,
    AgentThreadRecordRow,
    HumanReviewCaseEventRow,
    HumanReviewCaseRow,
)
from app.infrastructure.database.models.observability import (
    AgentRun,
    AgentRunStatus,
    AgentTraceEvent,
    OutboxEvent,
    OutboxStatus,
    TraceSource,
)


class SqlAlchemyAgentReliabilityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register_thread(
        self,
        *,
        thread_id: UUID,
        resident_id: UUID,
        property_id: UUID | None,
    ) -> ThreadRecord:
        now = datetime.now(UTC)
        row = await self._session.get(AgentThreadRecordRow, thread_id, with_for_update=True)
        if row is None:
            row = AgentThreadRecordRow(
                thread_id=thread_id,
                resident_id=resident_id,
                property_id=property_id,
                property_resolution_status=(
                    ThreadPropertyResolutionStatus.RESOLVED
                    if property_id is not None
                    else ThreadPropertyResolutionStatus.UNRESOLVED
                ),
                lifecycle_status=ThreadLifecycleStatus.ACTIVE,
                display_title=None,
                last_activity_at=now,
                created_at=now,
                updated_at=now,
                archived_at=None,
                archived_by_actor_id=None,
                version=1,
            )
            self._session.add(row)
            await self._session.flush()
        elif row.resident_id != resident_id:
            raise AgentReliabilityConflict("thread owner cannot change")
        elif (
            property_id is not None
            and row.property_id is not None
            and row.property_id != property_id
        ):
            raise AgentReliabilityConflict("thread property cannot change")
        elif property_id is not None and row.property_id is None:
            row.property_id = property_id
            row.property_resolution_status = ThreadPropertyResolutionStatus.RESOLVED
            row.updated_at = now
            row.version += 1
        return self._thread(row)

    async def finalize_run(self, command: FinalizeAgentRun) -> HumanReviewCaseRecord | None:
        run = await self._session.get(AgentRun, command.run_id, with_for_update=True)
        if run is None:
            raise AgentThreadNotFound("agent run does not exist")
        thread = await self._session.get(
            AgentThreadRecordRow,
            command.thread_id,
            with_for_update=True,
        )
        if thread is None:
            await self.register_thread(
                thread_id=command.thread_id,
                resident_id=command.resident_id,
                property_id=command.property_id,
            )
            thread = await self._session.get(
                AgentThreadRecordRow,
                command.thread_id,
                with_for_update=True,
            )
            assert thread is not None
        elif thread.resident_id != command.resident_id:
            raise AgentReliabilityConflict("thread owner cannot change")
        elif (
            command.property_id is not None
            and thread.property_id is not None
            and thread.property_id != command.property_id
        ):
            raise AgentReliabilityConflict("thread property cannot change")
        elif command.property_id is not None and thread.property_id is None:
            thread.property_id = command.property_id
            thread.property_resolution_status = ThreadPropertyResolutionStatus.RESOLVED
        if run.message_outcome is not None:
            if (
                run.message_outcome is not command.outcome
                or run.required_user_action is not command.required_user_action
            ):
                raise AgentReliabilityConflict("run already has another public result")
            return await self._active_case(command)

        now = datetime.now(UTC)
        if command.user_message_id is not None and command.user_message is not None:
            await self._append_message(
                thread,
                run_id=command.run_id,
                message_id=command.user_message_id,
                role=AgentMessageRole.USER,
                content=command.user_message,
                created_at=command.user_message_created_at or now,
            )
        await self._append_message(
            thread,
            run_id=command.run_id,
            message_id=uuid5(NAMESPACE_URL, f"fixflow:durable:{command.run_id}:ASSISTANT"),
            role=AgentMessageRole.ASSISTANT,
            content=command.assistant_message,
            created_at=now,
        )

        review_case: HumanReviewCaseRow | None = None
        if command.outcome is MessageOutcome.ESCALATED:
            review_case = await self._create_or_get_review(command, now)

        run.status = AgentRunStatus(command.agent_run_status)
        run.finished_at = now
        run.terminal_event_type = command.agent_run_terminal_event_type
        run.error_code = command.error_code
        run.message_outcome = command.outcome
        run.required_user_action = command.required_user_action
        await self._append_terminal_trace(run, command, now)

        thread.last_activity_at = now
        thread.updated_at = now
        thread.version += 1
        return self._case(review_case) if review_case is not None else None

    async def list_messages(self, thread_id: UUID) -> Sequence[DurableMessage]:
        rows = await self._session.scalars(
            select(AgentMessageRow)
            .where(AgentMessageRow.thread_id == thread_id)
            .order_by(AgentMessageRow.sequence_no)
        )
        return tuple(self._message(row) for row in rows)

    async def latest_public_result(self, thread_id: UUID) -> PublicRunResult | None:
        row = await self._session.scalar(
            select(AgentRun)
            .where(
                AgentRun.thread_id == thread_id,
                AgentRun.message_outcome.is_not(None),
            )
            .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
            .limit(1)
        )
        if row is None or row.message_outcome is None:
            return None
        return PublicRunResult(
            run_id=row.id,
            message_outcome=row.message_outcome,
            required_user_action=row.required_user_action,
        )

    async def list_threads(
        self,
        resident_id: UUID,
        *,
        lifecycle_status: ThreadLifecycleStatus | None,
        limit: int,
        offset: int,
    ) -> Sequence[ThreadRecord]:
        statement = select(AgentThreadRecordRow).where(
            AgentThreadRecordRow.resident_id == resident_id
        )
        if lifecycle_status is not None:
            statement = statement.where(AgentThreadRecordRow.lifecycle_status == lifecycle_status)
        rows = await self._session.scalars(
            statement.order_by(
                AgentThreadRecordRow.last_activity_at.desc(),
                AgentThreadRecordRow.thread_id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return tuple(self._thread(row) for row in rows)

    async def get_thread(self, thread_id: UUID, *, for_update: bool = False) -> ThreadRecord | None:
        statement = select(AgentThreadRecordRow).where(AgentThreadRecordRow.thread_id == thread_id)
        if for_update:
            statement = statement.with_for_update()
        row = await self._session.scalar(statement)
        return self._thread(row) if row is not None else None

    async def set_thread_lifecycle(
        self,
        *,
        thread_id: UUID,
        resident_id: UUID,
        lifecycle_status: ThreadLifecycleStatus,
        actor_id: UUID,
        expected_version: int,
    ) -> ThreadRecord:
        now = datetime.now(UTC)
        values: dict[str, object] = {
            "lifecycle_status": lifecycle_status,
            "updated_at": now,
            "version": expected_version + 1,
            "archived_at": now if lifecycle_status is ThreadLifecycleStatus.ARCHIVED else None,
            "archived_by_actor_id": (
                actor_id if lifecycle_status is ThreadLifecycleStatus.ARCHIVED else None
            ),
        }
        statement = (
            update(AgentThreadRecordRow)
            .where(
                AgentThreadRecordRow.thread_id == thread_id,
                AgentThreadRecordRow.resident_id == resident_id,
                AgentThreadRecordRow.version == expected_version,
            )
            .values(**values)
            .returning(AgentThreadRecordRow)
        )
        row = (await self._session.execute(statement)).scalar_one_or_none()
        if row is None:
            raise AgentReliabilityConflict("thread version conflict")
        return self._thread(row)

    async def list_human_review_cases(
        self,
        *,
        status: HumanReviewStatus | None,
        limit: int,
        offset: int,
    ) -> Sequence[HumanReviewCaseRecord]:
        statement = select(HumanReviewCaseRow)
        if status is not None:
            statement = statement.where(HumanReviewCaseRow.status == status)
        rows = await self._session.scalars(
            statement.order_by(
                HumanReviewCaseRow.priority.desc(),
                HumanReviewCaseRow.created_at.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return tuple(self._case(row) for row in rows)

    async def transition_human_review(
        self, command: HumanReviewTransition
    ) -> HumanReviewCaseRecord:
        row = await self._session.get(
            HumanReviewCaseRow,
            command.case_id,
            with_for_update=True,
        )
        if row is None:
            raise AgentThreadNotFound("human review case does not exist")
        if row.version != command.expected_version:
            raise AgentReliabilityConflict("human review version conflict")
        allowed = {
            HumanReviewStatus.OPEN: {
                HumanReviewStatus.CLAIMED,
                HumanReviewStatus.RESOLVED,
                HumanReviewStatus.DISMISSED,
            },
            HumanReviewStatus.CLAIMED: {
                HumanReviewStatus.OPEN,
                HumanReviewStatus.RESOLVED,
                HumanReviewStatus.DISMISSED,
            },
        }
        if command.target_status not in allowed.get(row.status, set()):
            raise AgentReliabilityConflict("invalid human review transition")

        before = row.status
        before_version = row.version
        now = datetime.now(UTC)
        row.status = command.target_status
        row.version += 1
        row.updated_at = now
        if command.target_status is HumanReviewStatus.CLAIMED:
            row.assigned_operator_id = command.actor_id
            row.claimed_at = now
        elif command.target_status is HumanReviewStatus.OPEN:
            row.assigned_operator_id = None
            row.claimed_at = None
        else:
            row.resolved_at = now
            row.resolution_code = command.resolution_code
            row.resolution_note = command.resolution_note

        self._session.add(
            HumanReviewCaseEventRow(
                case_id=row.id,
                sequence_no=row.version,
                from_status=before,
                to_status=row.status,
                action=self._transition_action(row.status),
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                trace_id=command.trace_id,
                version_before=before_version,
                version_after=row.version,
                reason_code=command.resolution_code,
                note=command.resolution_note,
                occurred_at=now,
            )
        )
        self._add_review_outbox(
            row,
            event_type=f"HUMAN_REVIEW_{row.status.value}",
            actor_type=command.actor_type,
            actor_id=command.actor_id,
            trace_id=command.trace_id,
            occurred_at=now,
        )
        await self._session.flush()
        return self._case(row)

    async def list_human_review_events(self, case_id: UUID) -> Sequence[HumanReviewEventRecord]:
        exists = await self._session.get(HumanReviewCaseRow, case_id)
        if exists is None:
            raise AgentThreadNotFound("human review case does not exist")
        rows = await self._session.scalars(
            select(HumanReviewCaseEventRow)
            .where(HumanReviewCaseEventRow.case_id == case_id)
            .order_by(HumanReviewCaseEventRow.sequence_no)
        )
        return tuple(self._case_event(row) for row in rows)

    async def reconcile_stalled_runs(self, *, older_than_seconds: int, limit: int) -> int:
        threshold = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        rows = await self._session.scalars(
            select(AgentRun)
            .where(
                AgentRun.status == AgentRunStatus.RUNNING,
                AgentRun.started_at < threshold,
            )
            .order_by(AgentRun.started_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        count = 0
        for run in rows:
            if run.thread_id is None or run.user_id is None:
                await self._fail_stalled_run(run, datetime.now(UTC))
                count += 1
                continue
            await self.register_thread(
                thread_id=run.thread_id,
                resident_id=run.user_id,
                property_id=run.property_id,
            )
            assistant = await self._session.scalar(
                select(AgentMessageRow).where(
                    AgentMessageRow.run_id == run.id,
                    AgentMessageRow.role == AgentMessageRole.ASSISTANT,
                )
            )
            active_case = await self._session.scalar(
                select(HumanReviewCaseRow).where(
                    HumanReviewCaseRow.source_run_id == run.id,
                    HumanReviewCaseRow.status.in_(
                        (HumanReviewStatus.OPEN, HumanReviewStatus.CLAIMED)
                    ),
                )
            )
            if active_case is not None:
                outcome = MessageOutcome.ESCALATED
                action = RequiredUserAction.CONTACT_OPERATOR
                assistant_text = assistant.content if assistant else "本次请求已转交物业人工处理。"
            elif assistant is not None:
                outcome = MessageOutcome.COMPLETED
                action = RequiredUserAction.NONE
                assistant_text = assistant.content
            else:
                outcome = MessageOutcome.FAILED
                action = RequiredUserAction.RETRY
                assistant_text = "本次请求处理超时且未确认完成，请重试或联系物业。"
            command = FinalizeAgentRun(
                run_id=run.id,
                thread_id=run.thread_id,
                trace_id=run.trace_id,
                resident_id=run.user_id,
                property_id=run.property_id,
                intent_version=run.final_intent_version or run.initial_intent_version or 1,
                user_message_id=None,
                user_message=None,
                user_message_created_at=None,
                assistant_message=assistant_text,
                outcome=outcome,
                required_user_action=action,
                agent_run_status=(
                    AgentRunStatus.COMPLETED.value
                    if outcome is MessageOutcome.COMPLETED
                    else AgentRunStatus.FAILED.value
                    if outcome is MessageOutcome.FAILED
                    else AgentRunStatus.COMPLETED.value
                ),
                agent_run_terminal_event_type=(
                    "run_completed"
                    if outcome in {MessageOutcome.COMPLETED, MessageOutcome.ESCALATED}
                    else "run_failed"
                ),
                message_event_type=(
                    "message.completed"
                    if outcome is MessageOutcome.COMPLETED
                    else "message.failed"
                    if outcome is MessageOutcome.FAILED
                    else "message.escalated"
                ),
                error_code="STALLED_RUN_RECONCILED",
            )
            await self._append_message(
                await self._locked_thread(run.thread_id),
                run_id=run.id,
                message_id=uuid5(NAMESPACE_URL, f"fixflow:stalled:{run.id}:ASSISTANT"),
                role=AgentMessageRole.ASSISTANT,
                content=assistant_text,
                created_at=datetime.now(UTC),
            )
            run.status = AgentRunStatus(command.agent_run_status)
            run.finished_at = datetime.now(UTC)
            run.terminal_event_type = command.agent_run_terminal_event_type
            run.error_code = command.error_code
            run.message_outcome = outcome
            run.required_user_action = action
            await self._append_terminal_trace(run, command, datetime.now(UTC))
            count += 1
        return count

    async def _fail_stalled_run(self, run: AgentRun, occurred_at: datetime) -> None:
        run.status = AgentRunStatus.FAILED
        run.finished_at = occurred_at
        run.terminal_event_type = "run_failed"
        run.error_code = "STALLED_RUN_MISSING_CONTEXT"
        run.message_outcome = MessageOutcome.FAILED
        run.required_user_action = RequiredUserAction.RETRY
        command = FinalizeAgentRun(
            run_id=run.id,
            thread_id=run.thread_id or run.id,
            trace_id=run.trace_id,
            resident_id=run.user_id or run.actor_id,
            property_id=run.property_id,
            intent_version=run.final_intent_version or run.initial_intent_version or 1,
            user_message_id=None,
            user_message=None,
            user_message_created_at=None,
            assistant_message="本次请求处理超时且缺少必要上下文，请重试。",
            outcome=MessageOutcome.FAILED,
            required_user_action=RequiredUserAction.RETRY,
            agent_run_status=AgentRunStatus.FAILED.value,
            agent_run_terminal_event_type="run_failed",
            message_event_type="message.failed",
            error_code="STALLED_RUN_MISSING_CONTEXT",
        )
        await self._append_terminal_trace(run, command, occurred_at)

    async def _append_message(
        self,
        thread: AgentThreadRecordRow,
        *,
        run_id: UUID,
        message_id: UUID,
        role: AgentMessageRole,
        content: str,
        created_at: datetime,
    ) -> None:
        existing = await self._session.scalar(
            select(AgentMessageRow).where(
                (AgentMessageRow.message_id == message_id)
                | ((AgentMessageRow.run_id == run_id) & (AgentMessageRow.role == role))
            )
        )
        if existing is not None:
            return
        sequence = (
            await self._session.scalar(
                select(func.max(AgentMessageRow.sequence_no)).where(
                    AgentMessageRow.thread_id == thread.thread_id
                )
            )
            or 0
        ) + 1
        self._session.add(
            AgentMessageRow(
                message_id=message_id,
                thread_id=thread.thread_id,
                run_id=run_id,
                role=role,
                content=content.strip(),
                sequence_no=sequence,
                created_at=created_at,
            )
        )
        await self._session.flush()

    async def _create_or_get_review(
        self, command: FinalizeAgentRun, now: datetime
    ) -> HumanReviewCaseRow:
        assert command.failure_stage is not None
        assert command.reason_code is not None
        dedupe_key = hashlib.sha256(
            f"{command.thread_id}:{command.intent_version}:{command.failure_stage.value}".encode()
        ).hexdigest()
        existing = await self._session.scalar(
            select(HumanReviewCaseRow).where(
                HumanReviewCaseRow.dedupe_key == dedupe_key,
                HumanReviewCaseRow.status.in_((HumanReviewStatus.OPEN, HumanReviewStatus.CLAIMED)),
            )
        )
        if existing is not None:
            return existing
        row = HumanReviewCaseRow(
            thread_id=command.thread_id,
            resident_id=command.resident_id,
            property_id=command.property_id,
            ticket_id=command.active_ticket_id,
            source_run_id=command.run_id,
            intent_version=command.intent_version,
            failure_stage=command.failure_stage,
            reason_code=command.reason_code,
            last_error_code=command.error_code,
            safety_level=command.safety_level,
            priority=self._priority(command),
            summary=command.assistant_message[:2000],
            status=HumanReviewStatus.OPEN,
            assigned_operator_id=None,
            dedupe_key=dedupe_key,
            version=1,
            created_at=now,
            updated_at=now,
            claimed_at=None,
            resolved_at=None,
            resolution_code=None,
            resolution_note=None,
        )
        self._session.add(row)
        await self._session.flush()
        self._session.add(
            HumanReviewCaseEventRow(
                case_id=row.id,
                sequence_no=1,
                from_status=None,
                to_status=HumanReviewStatus.OPEN,
                action="CREATED",
                actor_type=ActorType.SYSTEM,
                actor_id=command.resident_id,
                trace_id=command.trace_id,
                version_before=0,
                version_after=1,
                reason_code=command.reason_code,
                note=None,
                occurred_at=now,
            )
        )
        self._add_review_outbox(
            row,
            event_type="HUMAN_REVIEW_OPENED",
            actor_type=ActorType.SYSTEM,
            actor_id=command.resident_id,
            trace_id=command.trace_id,
            occurred_at=now,
        )
        return row

    async def _active_case(self, command: FinalizeAgentRun) -> HumanReviewCaseRecord | None:
        row = await self._session.scalar(
            select(HumanReviewCaseRow).where(
                HumanReviewCaseRow.source_run_id == command.run_id,
                HumanReviewCaseRow.status.in_((HumanReviewStatus.OPEN, HumanReviewStatus.CLAIMED)),
            )
        )
        return self._case(row) if row is not None else None

    async def _append_terminal_trace(
        self, run: AgentRun, command: FinalizeAgentRun, occurred_at: datetime
    ) -> None:
        public_terminal = await self._session.scalar(
            select(AgentTraceEvent).where(
                AgentTraceEvent.run_id == run.id,
                AgentTraceEvent.event_type.in_(
                    ("message.completed", "message.failed", "message.escalated")
                ),
            )
        )
        if public_terminal is not None:
            return
        lifecycle_terminal = await self._session.scalar(
            select(AgentTraceEvent).where(
                AgentTraceEvent.run_id == run.id,
                AgentTraceEvent.event_type.in_(
                    ("run_interrupted", "run_completed", "run_failed_safe", "run_failed")
                ),
            )
        )
        if lifecycle_terminal is None:
            run.last_sequence += 1
            self._session.add(
                self._terminal_event(
                    run,
                    sequence_number=run.last_sequence,
                    event_type=command.agent_run_terminal_event_type,
                    source=TraceSource.AGENT,
                    payload={"status": command.agent_run_status},
                    occurred_at=occurred_at,
                )
            )
        run.last_sequence += 1
        self._session.add(
            self._terminal_event(
                run,
                sequence_number=run.last_sequence,
                event_type=command.message_event_type,
                source=TraceSource.API,
                payload={
                    "message_outcome": command.outcome.value,
                    "required_user_action": command.required_user_action.value,
                },
                occurred_at=occurred_at,
            )
        )

    @staticmethod
    def _terminal_event(
        run: AgentRun,
        *,
        sequence_number: int,
        event_type: str,
        source: TraceSource,
        payload: dict[str, object],
        occurred_at: datetime,
    ) -> AgentTraceEvent:
        return AgentTraceEvent(
            event_key=hashlib.sha256(f"{run.id}:{event_type}".encode()).hexdigest(),
            run_id=run.id,
            thread_id=run.thread_id,
            trace_id=run.trace_id,
            sequence_number=sequence_number,
            source=source,
            event_type=event_type,
            node_name=None,
            operation_id=None,
            payload=payload,
            occurred_at=occurred_at,
        )

    async def _locked_thread(self, thread_id: UUID) -> AgentThreadRecordRow:
        row = await self._session.get(AgentThreadRecordRow, thread_id, with_for_update=True)
        if row is None:
            raise AgentThreadNotFound("thread does not exist")
        return row

    def _add_review_outbox(
        self,
        row: HumanReviewCaseRow,
        *,
        event_type: str,
        actor_type: ActorType,
        actor_id: UUID,
        trace_id: UUID,
        occurred_at: datetime,
    ) -> None:
        self._session.add(
            OutboxEvent(
                event_key=hashlib.sha256(
                    f"human-review:{row.id}:{row.version}:{event_type}".encode()
                ).hexdigest(),
                event_type=event_type,
                aggregate_type="HUMAN_REVIEW_CASE",
                aggregate_id=row.id,
                aggregate_version=row.version,
                operation_id=uuid5(
                    NAMESPACE_URL,
                    f"fixflow:human-review:{row.id}:{row.version}:{event_type}",
                ),
                trace_id=trace_id,
                run_id=row.source_run_id,
                thread_id=row.thread_id,
                actor_type=actor_type.value,
                actor_id=actor_id,
                payload={"case_id": str(row.id), "status": row.status.value},
                status=OutboxStatus.PENDING,
                occurred_at=occurred_at,
                available_at=occurred_at,
                claimed_by=None,
                claim_token=None,
                claim_expires_at=None,
                attempt_count=0,
                last_error_code=None,
                last_error_message=None,
                created_at=occurred_at,
                dispatched_at=None,
            )
        )

    @staticmethod
    def _priority(command: FinalizeAgentRun) -> int:
        if command.safety_level.value == "EMERGENCY":
            return 100
        if command.safety_level.value == "ELEVATED":
            return 70
        return 50

    @staticmethod
    def _transition_action(status: HumanReviewStatus) -> str:
        return {
            HumanReviewStatus.OPEN: "RELEASED",
            HumanReviewStatus.CLAIMED: "CLAIMED",
            HumanReviewStatus.RESOLVED: "RESOLVED",
            HumanReviewStatus.DISMISSED: "DISMISSED",
        }[status]

    @staticmethod
    def _thread(row: AgentThreadRecordRow) -> ThreadRecord:
        return ThreadRecord(
            thread_id=row.thread_id,
            resident_id=row.resident_id,
            property_id=row.property_id,
            property_resolution_status=row.property_resolution_status,
            lifecycle_status=row.lifecycle_status,
            display_title=row.display_title,
            last_activity_at=row.last_activity_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
            archived_at=row.archived_at,
            version=row.version,
        )

    @staticmethod
    def _message(row: AgentMessageRow) -> DurableMessage:
        return DurableMessage(
            message_id=row.message_id,
            thread_id=row.thread_id,
            run_id=row.run_id,
            role=row.role,
            content=row.content,
            sequence_no=row.sequence_no,
            created_at=row.created_at,
        )

    @staticmethod
    def _case(row: HumanReviewCaseRow) -> HumanReviewCaseRecord:
        return HumanReviewCaseRecord(
            case_id=row.id,
            thread_id=row.thread_id,
            resident_id=row.resident_id,
            property_id=row.property_id,
            ticket_id=row.ticket_id,
            source_run_id=row.source_run_id,
            intent_version=row.intent_version,
            failure_stage=row.failure_stage,
            reason_code=row.reason_code,
            last_error_code=row.last_error_code,
            safety_level=row.safety_level,
            priority=row.priority,
            summary=row.summary,
            status=row.status,
            assigned_operator_id=row.assigned_operator_id,
            dedupe_key=row.dedupe_key,
            version=row.version,
            created_at=row.created_at,
            updated_at=row.updated_at,
            claimed_at=row.claimed_at,
            resolved_at=row.resolved_at,
            resolution_code=row.resolution_code,
            resolution_note=row.resolution_note,
        )

    @staticmethod
    def _case_event(row: HumanReviewCaseEventRow) -> HumanReviewEventRecord:
        return HumanReviewEventRecord(
            event_id=row.id,
            case_id=row.case_id,
            sequence_no=row.sequence_no,
            from_status=row.from_status,
            to_status=row.to_status,
            action=row.action,
            actor_type=row.actor_type,
            actor_id=row.actor_id,
            trace_id=row.trace_id,
            version_before=row.version_before,
            version_after=row.version_after,
            reason_code=row.reason_code,
            note=row.note,
            occurred_at=row.occurred_at,
        )

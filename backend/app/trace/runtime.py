"""Concurrency-safe persistent Agent run and trace lifecycle."""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.models.observability import (
    AgentRun,
    AgentRunStatus,
    AgentTraceEvent,
    TraceSource,
)
from app.trace.models import AgentRunRecord, StartRun, TraceEventRecord, TracePayload
from app.trace.sanitizer import TraceSanitizer


class TraceConflict(RuntimeError):
    pass


class TraceRuntime:
    _LIFECYCLE_TERMINAL_EVENTS = frozenset(
        {"run_interrupted", "run_completed", "run_failed_safe", "run_failed"}
    )
    _POST_TERMINAL_AUDIT_SOURCES = frozenset({TraceSource.DOMAIN, TraceSource.OUTBOX})

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sanitizer: TraceSanitizer,
    ) -> None:
        self._sessions = session_factory
        self._sanitizer = sanitizer

    async def start_run(self, command: StartRun) -> AgentRunRecord:
        async with self._sessions() as session, session.begin():
            existing = await session.get(AgentRun, command.run_id)
            if existing is not None:
                return AgentRunRecord.model_validate(existing)
            run = AgentRun(
                id=command.run_id,
                thread_id=command.thread_id,
                trace_id=command.trace_id,
                trigger=command.trigger,
                status=AgentRunStatus.RUNNING,
                actor_type=command.actor_type,
                actor_id=command.actor_id,
                user_id=command.user_id,
                property_id=command.property_id,
                initial_intent_version=command.initial_intent_version,
                final_intent_version=None,
                last_sequence=1,
                started_at=command.started_at,
                finished_at=None,
                terminal_event_type=None,
                error_code=None,
            )
            session.add(run)
            session.add(
                self._event_model(
                    event_key=self.event_key(command.run_id, "run_started"),
                    run=run,
                    sequence=1,
                    source=TraceSource.AGENT,
                    event_type="run_started",
                    node_name=None,
                    operation_id=None,
                    payload=TracePayload(run_status=AgentRunStatus.RUNNING.value),
                    occurred_at=command.started_at,
                )
            )
            await session.flush()
            return AgentRunRecord.model_validate(run)

    async def append_event(
        self,
        *,
        event_key: str,
        run_id: UUID | None,
        thread_id: UUID | None,
        trace_id: UUID,
        source: TraceSource,
        event_type: str,
        payload: TracePayload,
        occurred_at: datetime,
        node_name: str | None = None,
        operation_id: UUID | None = None,
    ) -> TraceEventRecord:
        async with self._sessions() as session, session.begin():
            existing = await self._event_by_key(session, event_key)
            if existing is not None:
                return TraceEventRecord.model_validate(existing)
            run = None
            sequence = None
            if run_id is not None:
                run = await session.scalar(
                    select(AgentRun).where(AgentRun.id == run_id).with_for_update()
                )
                if run is None:
                    raise TraceConflict("trace run not found")
                existing = await self._event_by_key(session, event_key)
                if existing is not None:
                    return TraceEventRecord.model_validate(existing)
                if event_type in self._LIFECYCLE_TERMINAL_EVENTS:
                    raise TraceConflict("lifecycle terminal events must use finish_run")
                if (
                    run.status is not AgentRunStatus.RUNNING
                    and source not in self._POST_TERMINAL_AUDIT_SOURCES
                ):
                    raise TraceConflict(
                        "control-plane trace events are closed after run termination"
                    )
                run.last_sequence += 1
                sequence = run.last_sequence
                thread_id = run.thread_id
                trace_id = run.trace_id
            event = self._event_model(
                event_key=event_key,
                run=run,
                sequence=sequence,
                source=source,
                event_type=event_type,
                node_name=node_name,
                operation_id=operation_id,
                payload=payload,
                occurred_at=occurred_at,
                thread_id=thread_id,
                trace_id=trace_id,
            )
            session.add(event)
            await session.flush()
            return TraceEventRecord.model_validate(event)

    async def finish_run(
        self,
        run_id: UUID,
        *,
        status: AgentRunStatus,
        occurred_at: datetime,
        final_intent_version: int | None = None,
        error_code: str | None = None,
    ) -> AgentRunRecord:
        if status is AgentRunStatus.RUNNING:
            raise ValueError("finish status must be terminal")
        async with self._sessions() as session, session.begin():
            run = await session.scalar(
                select(AgentRun).where(AgentRun.id == run_id).with_for_update()
            )
            if run is None:
                raise TraceConflict("trace run not found")
            if run.status is not AgentRunStatus.RUNNING:
                if run.status is not status:
                    raise TraceConflict("run already finished with another status")
                return AgentRunRecord.model_validate(run)
            event_type = {
                AgentRunStatus.INTERRUPTED: "run_interrupted",
                AgentRunStatus.COMPLETED: "run_completed",
                AgentRunStatus.FAILED_SAFE: "run_failed_safe",
                AgentRunStatus.FAILED: "run_failed",
            }[status]
            run.last_sequence += 1
            session.add(
                self._event_model(
                    event_key=self.event_key(run_id, event_type),
                    run=run,
                    sequence=run.last_sequence,
                    source=TraceSource.AGENT,
                    event_type=event_type,
                    node_name=None,
                    operation_id=None,
                    payload=TracePayload(run_status=status.value, error_code=error_code),
                    occurred_at=occurred_at,
                )
            )
            # The lifecycle event, final status, terminal marker, and sequence
            # allocation share this transaction. Either all become visible or
            # the run remains RUNNING with no terminal trace.
            run.status = status
            run.finished_at = occurred_at
            run.terminal_event_type = event_type
            run.final_intent_version = final_intent_version
            run.error_code = error_code
            await session.flush()
            return AgentRunRecord.model_validate(run)

    async def get_run(self, run_id: UUID) -> AgentRunRecord | None:
        async with self._sessions() as session:
            run = await session.get(AgentRun, run_id)
            return AgentRunRecord.model_validate(run) if run is not None else None

    async def list_runs(
        self, thread_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> tuple[AgentRunRecord, ...]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(AgentRun)
                .where(AgentRun.thread_id == thread_id)
                .order_by(AgentRun.started_at.desc(), AgentRun.id.desc())
                .limit(limit)
                .offset(offset)
            )
            return tuple(AgentRunRecord.model_validate(row) for row in rows)

    async def list_events(
        self,
        run_id: UUID,
        *,
        source: TraceSource | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[TraceEventRecord, ...]:
        async with self._sessions() as session:
            statement: Select[tuple[AgentTraceEvent]] = select(AgentTraceEvent).where(
                AgentTraceEvent.run_id == run_id
            )
            if source is not None:
                statement = statement.where(AgentTraceEvent.source == source)
            rows = await session.scalars(
                statement.order_by(AgentTraceEvent.sequence_number).limit(limit).offset(offset)
            )
            return tuple(TraceEventRecord.model_validate(row) for row in rows)

    @staticmethod
    def event_key(run_id: UUID, event_type: str, suffix: str = "") -> str:
        return hashlib.sha256(f"{run_id}|{event_type}|{suffix}".encode()).hexdigest()

    async def _event_by_key(self, session: AsyncSession, event_key: str) -> AgentTraceEvent | None:
        return cast(
            AgentTraceEvent | None,
            await session.scalar(
                select(AgentTraceEvent).where(AgentTraceEvent.event_key == event_key)
            ),
        )

    def _event_model(
        self,
        *,
        event_key: str,
        run: AgentRun | None,
        sequence: int | None,
        source: TraceSource,
        event_type: str,
        node_name: str | None,
        operation_id: UUID | None,
        payload: TracePayload,
        occurred_at: datetime,
        thread_id: UUID | None = None,
        trace_id: UUID | None = None,
    ) -> AgentTraceEvent:
        resolved_trace_id = run.trace_id if run is not None else trace_id
        if resolved_trace_id is None:
            raise ValueError("trace_id is required for an external trace event")
        return AgentTraceEvent(
            id=uuid4(),
            event_key=event_key,
            run_id=run.id if run is not None else None,
            thread_id=run.thread_id if run is not None else thread_id,
            trace_id=resolved_trace_id,
            sequence_number=sequence,
            source=source,
            event_type=event_type,
            node_name=node_name,
            operation_id=operation_id,
            payload=self._sanitizer.sanitize(payload),
            occurred_at=occurred_at,
        )

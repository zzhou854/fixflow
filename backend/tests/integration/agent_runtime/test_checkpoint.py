"""Official checkpointer setup and cross-instance Interrupt/Resume evidence."""

from typing import TypedDict, cast

import asyncpg
import pytest
from app.agent_runtime.checkpoint import open_postgres_checkpointer
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from sqlalchemy.engine import make_url


class CounterState(TypedDict):
    value: int


def _graph(
    checkpointer: BaseCheckpointSaver[str],
) -> CompiledStateGraph[CounterState, None, CounterState, CounterState]:
    def pause(state: CounterState) -> CounterState:
        value = interrupt({"kind": "TEST", "value": state["value"]})
        return {"value": state["value"] + int(value)}

    graph = StateGraph(CounterState)
    graph.add_node("pause", pause)
    graph.add_edge(START, "pause")
    graph.add_edge("pause", END)
    return graph.compile(checkpointer=checkpointer)


@pytest.mark.asyncio
async def test_postgres_checkpoint_survives_new_graph_instance(
    empty_database_url: str,
) -> None:
    url = make_url(empty_database_url).set(drivername="postgresql").render_as_string(False)
    config = RunnableConfig(configurable={"thread_id": "checkpoint-restart-test"})
    async with open_postgres_checkpointer(url) as first_saver:
        first = _graph(first_saver)
        await first.ainvoke(CounterState(value=2), config)
        assert (await first.aget_state(config)).next == ("pause",)

    async with open_postgres_checkpointer(url) as second_saver:
        second = _graph(second_saver)
        command: Command[object] = Command(resume=3)
        result = cast(CounterState, await second.ainvoke(command, config))
        assert result["value"] == 5
        assert (await second.aget_state(config)).next == ()

    connection = await asyncpg.connect(url)
    try:
        rows = await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        names = {str(row["tablename"]) for row in rows}
        assert {"checkpoints", "checkpoint_writes", "checkpoint_migrations"} <= names
        assert "repair_tickets" not in names
    finally:
        await connection.close()

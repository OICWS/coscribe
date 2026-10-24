from pathlib import Path

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph

from coscribe.runtime_lg import CheckpointConversations
from coscribe.tools import SCHEDULED_THREAD_PREFIX


async def test_the_transcript_is_the_user_and_assistant_text_of_a_real_checkpoint(
    tmp_path: Path,
) -> None:
    graph = StateGraph(MessagesState)
    graph.add_node("reply", lambda state: {"messages": [AIMessage(content="Totals are in F.")]})
    graph.add_edge(START, "reply")
    graph.add_edge("reply", END)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.db")) as checkpointer:
        agent = graph.compile(checkpointer=checkpointer)
        for thread in ("b", f"{SCHEDULED_THREAD_PREFIX}x"):
            await agent.ainvoke(
                {"messages": [HumanMessage(content="Where are the totals?")]},
                {"configurable": {"thread_id": thread}},
            )
        source = CheckpointConversations(checkpointer)

        ids = await source.thread_ids()
        transcript = await source.transcript("b")
        missing = await source.transcript("nobody")

    assert ids == ["b"]
    assert transcript is not None and transcript.updated_at
    assert transcript.messages == [
        ("user", "Where are the totals?"),
        ("assistant", "Totals are in F."),
    ]
    assert missing is None

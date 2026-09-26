# ruff: noqa: E402
"""Tests for runtime_lg/tool_deferral.py and its wiring into
build_langgraph_agent (defer_tools=True) -- see that module's own
docstring for the design and what was verified live (a throwaway
script, not part of this suite) before writing it for real.
"""

import json
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from coscribe.runtime.types import tool_metadata
from coscribe.runtime_lg.agent import build_langgraph_agent
from coscribe.runtime_lg.tool_deferral import (
    SEARCH_TOOLS_NAME,
    _discovered_tool_names,
    _score,
    _tokenize,
    build_search_tools_tool,
)


def read_thing(x: str) -> str:
    """A harmless read tool."""
    return f"read: {x}"


def write_pptx_chart(text: str) -> str:
    """Add a chart to a pptx slide."""
    return f"chart: {text}"


def edit_pptx_theme(color: str) -> str:
    """Edit a pptx theme color."""
    return f"theme: {color}"


def unrelated_thing() -> str:
    """Do something completely unrelated to presentations."""
    return "unrelated"


class FakeToolCallingChatModel(BaseChatModel):
    """Same shape as other test files' identical class, plus recording
    every bind_tools() call's tool names -- the one thing this file
    actually needs to verify (what got advertised on each call)."""

    responses: list[AIMessage]
    i: int = 0
    bound_tool_names: list[list[str]] = []

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        self.bound_tool_names.append(sorted(getattr(t, "name", "") for t in tools))
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        message = self.responses[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Any:
        message = self.responses[self.i]
        self.i += 1
        chunk = AIMessageChunk(content=message.content or "", tool_calls=message.tool_calls)
        yield ChatGenerationChunk(message=chunk)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling-chat-model"


# -- Scoring / tokenizing -----------------------------------------------


def test_tokenize_drops_stopwords_and_short_tokens() -> None:
    assert _tokenize("Add a Chart to the Slide") == ["add", "chart", "slide"]


def test_score_rewards_a_name_substring_match() -> None:
    from coscribe.runtime_lg.tool_deferral import _build_entry

    entry = _build_entry(write_pptx_chart)
    on_topic = _score(entry, _tokenize("pptx chart"), "pptx chart")
    off_topic = _score(entry, _tokenize("send an email"), "send an email")
    assert on_topic > off_topic
    assert off_topic == 0


# -- search_tools tool ---------------------------------------------------


def test_search_tools_returns_ranked_json_matches() -> None:
    search_tools = build_search_tools_tool([write_pptx_chart, edit_pptx_theme, unrelated_thing])
    result = json.loads(search_tools("pptx"))
    names = [entry["name"] for entry in result]
    assert "write_pptx_chart" in names
    assert "edit_pptx_theme" in names
    assert "unrelated_thing" not in names


def test_search_tools_returns_empty_list_for_no_match() -> None:
    search_tools = build_search_tools_tool([write_pptx_chart])
    result = json.loads(search_tools("xyzzy_no_such_thing"))
    assert result == []


class _FakeTool:
    """A minimal stand-in with just the attributes _build_entry actually
    reads (`.name`/`.description`) -- lighter than 20 real functions for
    a test that only needs the count, not any of their behavior."""

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description


def test_search_tools_caps_at_max_results() -> None:
    many_tools = [_FakeTool(f"pptx_tool_{i}", "A pptx tool.") for i in range(20)]
    search_tools = build_search_tools_tool(many_tools)
    result = json.loads(search_tools("pptx"))
    assert len(result) <= 8


def test_search_tools_leaves_out_weak_matches() -> None:
    tools = [
        _FakeTool("run_python_script", "Run a Python script."),
        _FakeTool("add_pptx_hyperlink", "Link text on a slide to a script or page. " * 5),
    ]
    search_tools = build_search_tools_tool(tools)
    names = [entry["name"] for entry in json.loads(search_tools("run python script"))]
    assert names == ["run_python_script"]


# -- _discovered_tool_names -----------------------------------------------


def test_discovered_tool_names_parses_search_tools_results() -> None:
    from langchain_core.messages import ToolMessage

    messages = [
        ToolMessage(
            content=json.dumps([{"name": "write_pptx_chart", "description": "x"}]),
            tool_call_id="c1",
            name=SEARCH_TOOLS_NAME,
        ),
        ToolMessage(content="not json at all", tool_call_id="c2", name=SEARCH_TOOLS_NAME),
        ToolMessage(content="irrelevant", tool_call_id="c3", name="some_other_tool"),
    ]
    assert _discovered_tool_names(messages) == ["write_pptx_chart"]


def test_found_tools_are_sent_after_every_tool_already_sent() -> None:
    from types import SimpleNamespace

    from langchain_core.messages import ToolMessage

    from coscribe.runtime_lg.tool_deferral import DeferredToolMiddleware

    def found(*names: str) -> ToolMessage:
        entries = [{"name": name, "description": ""} for name in names]
        return ToolMessage(content=json.dumps(entries), tool_call_id="c", name=SEARCH_TOOLS_NAME)

    tools = [SimpleNamespace(name=name) for name in ["a", "x", "b", "y", "z"]]
    request = SimpleNamespace(tools=tools, messages=[found("z"), found("x", "z")])
    sent = DeferredToolMiddleware(["a", "b"])._filtered_tools(request)  # type: ignore[arg-type]

    assert [tool.name for tool in sent] == ["a", "b", "z", "x"]


# -- End-to-end: build_langgraph_agent(defer_tools=True) ------------------


async def test_deferred_tool_is_hidden_until_search_tools_finds_it() -> None:
    search_call = ToolCall(name="search_tools", args={"query": "pptx chart"}, id="c1")
    write_call = ToolCall(name="write_pptx_chart", args={"text": "hi"}, id="c2")
    model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[search_call]),
            AIMessage(content="", tool_calls=[write_call]),
            AIMessage(content="done"),
        ]
    )
    agent = build_langgraph_agent(
        model,
        [read_thing, write_pptx_chart, edit_pptx_theme, unrelated_thing],
        "test",
        checkpointer=InMemorySaver(),
        defer_tools=True,
        core_tool_names={"read_thing"},
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "add a chart"}]},
        config={"configurable": {"thread_id": "t1"}},
    )
    assert result["messages"][-1].content == "done"
    first_call_tools, second_call_tools, third_call_tools = model.bound_tool_names
    assert "write_pptx_chart" not in first_call_tools
    assert "edit_pptx_theme" not in first_call_tools
    assert "unrelated_thing" not in first_call_tools
    assert "read_thing" in first_call_tools
    assert SEARCH_TOOLS_NAME in first_call_tools
    assert "write_pptx_chart" in second_call_tools
    assert "write_pptx_chart" in third_call_tools


async def test_deferred_and_approval_gated_tool_still_pauses_once_discovered() -> None:
    risky_tool = tool_metadata(write_pptx_chart, risk_category="WRITE_LOCAL")
    search_call = ToolCall(name="search_tools", args={"query": "chart"}, id="c1")
    write_call = ToolCall(name="write_pptx_chart", args={"text": "hi"}, id="c2")
    model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[search_call]),
            AIMessage(content="", tool_calls=[write_call]),
            AIMessage(content="done"),
        ]
    )
    agent = build_langgraph_agent(
        model,
        [read_thing, risky_tool],
        "test",
        checkpointer=InMemorySaver(),
        defer_tools=True,
        core_tool_names={"read_thing"},
    )
    config = {"configurable": {"thread_id": "t1"}}
    agent.invoke(
        {"messages": [{"role": "user", "content": "add a chart"}]}, config=config
    )
    assert agent.get_state(config).next, "should be paused on approval for the discovered tool"

    # Same resume shape session.py's own _resolve_pending_approvals uses:
    # keyed by the interrupt's own id (not the tool_call_id), value a
    # {"decisions": [...]} list with one decision per action_request.
    interrupt = agent.get_state(config).tasks[0].interrupts[0]
    action_requests = interrupt.value.get("action_requests", [])
    decisions = [{"type": "approve"} for _ in action_requests]
    resumed = agent.invoke(
        Command(resume={interrupt.id: {"decisions": decisions}}), config=config
    )
    assert resumed["messages"][-1].content == "done"


async def test_core_tools_never_hidden_even_with_no_search() -> None:
    model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    agent = build_langgraph_agent(
        model,
        [read_thing, write_pptx_chart],
        "test",
        checkpointer=InMemorySaver(),
        defer_tools=True,
        core_tool_names={"read_thing"},
    )
    agent.invoke(
        {"messages": [{"role": "user", "content": "hello"}]},
        config={"configurable": {"thread_id": "t1"}},
    )
    (first_call_tools,) = model.bound_tool_names
    assert "read_thing" in first_call_tools
    assert SEARCH_TOOLS_NAME in first_call_tools
    assert "write_pptx_chart" not in first_call_tools


async def test_defer_tools_false_binds_every_tool_from_the_start() -> None:
    model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    agent = build_langgraph_agent(
        model,
        [read_thing, write_pptx_chart],
        "test",
        checkpointer=InMemorySaver(),
        defer_tools=False,
    )
    agent.invoke(
        {"messages": [{"role": "user", "content": "hello"}]},
        config={"configurable": {"thread_id": "t1"}},
    )
    (first_call_tools,) = model.bound_tool_names
    assert "write_pptx_chart" in first_call_tools
    assert SEARCH_TOOLS_NAME not in first_call_tools

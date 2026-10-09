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
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolCall, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from coscribe.runtime.types import tool_metadata
from coscribe.runtime_lg.agent import build_langgraph_agent
from coscribe.runtime_lg.tool_deferral import (
    SEARCH_TOOLS_NAME,
    USE_TOOL_NAME,
    _score,
    _tokenize,
    build_search_tools_tool,
    run_found_tools_directly,
    show_unbound_calls_as_use_tool,
    unwrap_use_tool_call,
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
    seen_messages: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        self.bound_tool_names.append(sorted(getattr(t, "name", "") for t in tools))
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        self.seen_messages.append(list(messages))
        message = self.responses[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Any:
        self.seen_messages.append(list(messages))
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


def _FakeTool(name: str, description: str) -> Any:
    """A real, argument-less tool: lighter than 20 real functions for a test
    that only needs names and descriptions."""
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(func=lambda: "", name=name, description=description)


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


# -- use_tool translation -------------------------------------------------


def test_search_tools_returns_each_matchs_parameter_schema() -> None:
    search_tools = build_search_tools_tool([write_pptx_chart, unrelated_thing])
    (match,) = json.loads(search_tools("chart"))
    assert match["name"] == "write_pptx_chart"
    assert match["parameters"]["properties"]["text"]["type"] == "string"
    assert match["parameters"]["required"] == ["text"]


def test_unwrap_use_tool_call() -> None:
    assert unwrap_use_tool_call(USE_TOOL_NAME, {"name": "a", "arguments": {"x": 1}}) == (
        "a",
        {"x": 1},
    )
    assert unwrap_use_tool_call(USE_TOOL_NAME, {"name": "a", "arguments": '{"x": 1}'}) == (
        "a",
        {"x": 1},
    )
    assert unwrap_use_tool_call("a", {"x": 1}) == ("a", {"x": 1})
    for malformed in (
        {"name": "a", "arguments": "not json"},
        {"name": "a"},
        {"name": USE_TOOL_NAME, "arguments": {}},
    ):
        assert unwrap_use_tool_call(USE_TOOL_NAME, malformed) == (USE_TOOL_NAME, malformed)


def test_use_tool_call_to_a_known_tool_becomes_the_real_call() -> None:
    message = AIMessage(
        content="",
        tool_calls=[
            ToolCall(name=USE_TOOL_NAME, args={"name": "a", "arguments": {"x": 1}}, id="c1"),
            ToolCall(name=USE_TOOL_NAME, args={"name": "nope", "arguments": {}}, id="c2"),
            ToolCall(name="b", args={}, id="c3"),
        ],
    )
    rewritten = run_found_tools_directly(message, {"a", "b"})
    names = [(c["name"], c["args"]) for c in rewritten.tool_calls]
    assert names == [
        ("a", {"x": 1}),
        (USE_TOOL_NAME, {"name": "nope", "arguments": {}}),
        ("b", {}),
    ]


def test_stored_calls_to_unbound_tools_read_back_as_use_tool() -> None:
    history: list[BaseMessage] = [
        AIMessage(
            content="",
            tool_calls=[
                ToolCall(name="a", args={"x": 1}, id="c1"),
                ToolCall(name="core", args={}, id="c2"),
            ],
        ),
        ToolMessage(content="r1", tool_call_id="c1", name="a"),
        ToolMessage(content="r2", tool_call_id="c2", name="core"),
    ]
    shown = show_unbound_calls_as_use_tool(history, {"core", SEARCH_TOOLS_NAME, USE_TOOL_NAME})

    assert [(c["name"], c["args"]) for c in shown[0].tool_calls] == [  # type: ignore[attr-defined]
        (USE_TOOL_NAME, {"name": "a", "arguments": {"x": 1}}),
        ("core", {}),
    ]
    assert [m.name for m in shown[1:]] == [USE_TOOL_NAME, "core"]  # type: ignore[attr-defined]
    assert history[0].tool_calls[0]["name"] == "a", "the stored history is left alone"  # type: ignore[attr-defined]


def test_provider_payloads_carry_the_use_tool_form() -> None:
    """The adapters read a call from more than one field; every one of them
    must say use_tool, or the request contradicts itself."""
    from langchain_anthropic import ChatAnthropic
    from langchain_openai import ChatOpenAI

    call = ToolCall(name="a", args={"x": 1}, id="c1")
    history: list[BaseMessage] = [
        AIMessage(
            content=[{"type": "tool_use", "id": "c1", "name": "a", "input": {"x": 1}}],
            tool_calls=[call],
            additional_kwargs={
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "a", "arguments": '{"x": 1}'},
                    }
                ]
            },
        ),
        ToolMessage(content="r", tool_call_id="c1", name="a"),
    ]
    shown = show_unbound_calls_as_use_tool(history, {USE_TOOL_NAME})

    openai_payload = ChatOpenAI(model="m", api_key="k")._get_request_payload(shown)  # type: ignore[arg-type]
    (sent,) = openai_payload["messages"][0]["tool_calls"]
    assert sent["function"]["name"] == USE_TOOL_NAME
    assert json.loads(sent["function"]["arguments"]) == {"name": "a", "arguments": {"x": 1}}

    anthropic_payload = ChatAnthropic(model="claude-sonnet-5-5", api_key="k")._get_request_payload(  # type: ignore[arg-type]
        shown
    )
    (block,) = [
        b for b in anthropic_payload["messages"][0]["content"] if b.get("type") == "tool_use"
    ]
    assert block["name"] == USE_TOOL_NAME
    assert block["input"] == {"name": "a", "arguments": {"x": 1}}


# -- End-to-end: build_langgraph_agent(defer_tools=True) ------------------


async def test_the_tool_list_never_changes_and_a_found_tool_runs_under_its_real_name() -> None:
    search_call = ToolCall(name="search_tools", args={"query": "pptx chart"}, id="c1")
    use_call = ToolCall(
        name=USE_TOOL_NAME,
        args={"name": "write_pptx_chart", "arguments": {"text": "hi"}},
        id="c2",
    )
    model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[search_call]),
            AIMessage(content="", tool_calls=[use_call]),
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
    fixed = sorted(["read_thing", SEARCH_TOOLS_NAME, USE_TOOL_NAME])
    assert model.bound_tool_names == [fixed, fixed, fixed]
    # Stored under the real name, with the tool's real output.
    stored_call = result["messages"][3]
    assert [(c["name"], c["args"]) for c in stored_call.tool_calls] == [
        ("write_pptx_chart", {"text": "hi"})
    ]
    assert (result["messages"][4].name, result["messages"][4].content) == (
        "write_pptx_chart",
        "chart: hi",
    )
    # The model reads its own call back in the form it wrote it.
    shown = [m for m in model.seen_messages[2] if isinstance(m, AIMessage) and m.tool_calls]
    assert [c["name"] for m in shown for c in m.tool_calls] == [SEARCH_TOOLS_NAME, USE_TOOL_NAME]
    assert shown[1].tool_calls[0]["args"] == {
        "name": "write_pptx_chart",
        "arguments": {"text": "hi"},
    }


async def test_deferred_and_approval_gated_tool_still_pauses_once_discovered() -> None:
    risky_tool = tool_metadata(write_pptx_chart, risk_category="WRITE_LOCAL")
    search_call = ToolCall(name="search_tools", args={"query": "chart"}, id="c1")
    write_call = ToolCall(
        name=USE_TOOL_NAME,
        args={"name": "write_pptx_chart", "arguments": {"text": "hi"}},
        id="c2",
    )
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
    pending = agent.get_state(config).tasks[0].interrupts[0].value["action_requests"]
    assert [(r["name"], r["args"]) for r in pending] == [("write_pptx_chart", {"text": "hi"})]

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


async def test_use_tool_with_an_unknown_name_gets_an_error_back() -> None:
    wrong = ToolCall(
        name=USE_TOOL_NAME, args={"name": "no_such_tool", "arguments": {}}, id="c1"
    )
    model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[wrong]), AIMessage(content="done")]
    )
    agent = build_langgraph_agent(
        model,
        [read_thing, write_pptx_chart],
        "test",
        checkpointer=InMemorySaver(),
        defer_tools=True,
        core_tool_names={"read_thing"},
    )
    result = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"configurable": {"thread_id": "t1"}},
    )

    assert "no tool named 'no_such_tool'" in result["messages"][2].content
    assert result["messages"][-1].content == "done"


async def test_a_streamed_turn_announces_the_real_tool_name() -> None:
    """The session reads tool rows and approvals from the finished model
    message, which must already carry the real name."""
    use_call = ToolCall(
        name=USE_TOOL_NAME,
        args={"name": "write_pptx_chart", "arguments": {"text": "hi"}},
        id="c1",
    )
    model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[use_call]), AIMessage(content="done")]
    )
    agent = build_langgraph_agent(
        model,
        [read_thing, write_pptx_chart],
        "test",
        checkpointer=InMemorySaver(),
        defer_tools=True,
        core_tool_names={"read_thing"},
    )
    announced: list[str] = []
    async for update in agent.astream(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"configurable": {"thread_id": "t1"}},
        stream_mode="updates",
    ):
        for node_update in update.values():
            if not isinstance(node_update, dict):
                continue
            for message in node_update.get("messages", []):
                if isinstance(message, AIMessage):
                    announced += [call["name"] for call in message.tool_calls]

    assert announced == ["write_pptx_chart"]


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


# -- Which tools a session binds up front, per provider -------------------

_DEEPSEEK = {"deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": "k"}}


def test_keeps_tool_list_fixed_only_for_deepseeks_own_host() -> None:
    from coscribe.runtime_lg.providers import keeps_tool_list_fixed

    assert keeps_tool_list_fixed("deepseek:deepseek-flash", _DEEPSEEK)
    renamed = {"ds": {"base_url": "https://api.deepseek.com", "api_key": "k"}}
    assert keeps_tool_list_fixed("ds:deepseek-v4-pro", renamed)
    for host in ("https://deepseek.com.example.org/v1", "https://notdeepseek.com/v1"):
        assert not keeps_tool_list_fixed("x:m", {"x": {"base_url": host, "api_key": "k"}})
    assert not keeps_tool_list_fixed("anthropic:claude-opus-5-5", _DEEPSEEK)


class _Socket:
    async def send_json(self, data: dict[str, Any]) -> None:
        pass


class _ContextWindow:
    def get_context_window(self, model: str) -> int:
        return 1_000_000


def _connector_tool() -> str:
    """A tool from an MCP connector."""
    return "ok"


async def _first_request_tools(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, model_string: str, extra_tools: list[Any]
) -> list[str]:
    from coscribe.config import Settings
    from coscribe.conversation.session import ChatSessionLG
    from coscribe.runtime import empty_hooks_config

    model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    monkeypatch.setattr(
        "coscribe.conversation.session.resolve_chat_model",
        lambda name, custom_providers=None: model,
    )
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        default_model=model_string,
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        skills_dir=tmp_path / "skills",
        memory_path=tmp_path / "MEMORY.md",
        auto_title_threads=False,
    )
    session = ChatSessionLG(
        thread_id="t",
        settings=settings,
        context_window_client=_ContextWindow(),
        custom_providers=_DEEPSEEK,
        extra_tools=extra_tools,
        checkpointer=InMemorySaver(),
        hooks_config=empty_hooks_config(),
        enabled_skill_names=set(),
    )
    await session.handle_user_message("hello", _Socket())  # type: ignore[arg-type]
    return model.bound_tool_names[0]


async def test_deepseek_binds_every_built_in_tool_from_the_first_request(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Finding a tool changes the tool list, which is sent before the
    conversation: on DeepSeek each discovery re-read the whole conversation
    uncached -- 22-56% of a long task's cost."""
    tools = await _first_request_tools(tmp_path, monkeypatch, "deepseek:deepseek-flash", [])

    assert {"write_pptx", "add_pptx_chart", "run_python_script", "write_docx"} <= set(tools)
    assert SEARCH_TOOLS_NAME not in tools


async def test_deepseek_still_defers_connector_tools(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = await _first_request_tools(
        tmp_path, monkeypatch, "deepseek:deepseek-flash", [_connector_tool]
    )

    assert "write_pptx" in tools and SEARCH_TOOLS_NAME in tools
    assert "_connector_tool" not in tools


async def test_other_providers_keep_deferring_built_in_tools(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = await _first_request_tools(tmp_path, monkeypatch, "anthropic:claude-opus-5-5", [])

    assert "write_pptx" not in tools and SEARCH_TOOLS_NAME in tools

"""Tests for runtime_lg/agent.py's build_langgraph_agent -- specifically the
max_turns/auto_compact_tokens wiring (Settings.max_turns/
auto_compact_threshold's runtime_lg equivalents, see config.py's
docstrings and runtime_lg/README.md's "max_turns and auto_compact_threshold"
section), plus AnthropicPromptCachingMiddleware wiring (ROADMAP.md's Phase
0 caching entry). The max_turns / ModelCallLimitMiddleware regression this
surfaced in _stream_turn (a middleware-injected AIMessage silently dropped)
is covered end-to-end in tests/test_web.py instead, since it's a
web/session.py bug, not an agent.py one -- this file only exercises
build_langgraph_agent directly, with no web layer involved.

Needs the langgraph_spike extra installed -- skips cleanly via
importorskip rather than failing collection when it isn't present.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from anthropic.types import Message as AnthropicMessage
from anthropic.types import TextBlock as AnthropicTextBlock
from anthropic.types import Usage as AnthropicUsage
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from coscribe.runtime_lg.agent import build_langgraph_agent


class _FakeModel(BaseChatModel):
    """Minimal scripted chat model -- only needs sync `_generate` since
    every call in this file goes through `.invoke()`, not `.astream()`."""

    responses: list[AIMessage]
    i: int = 0

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self.responses[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    @property
    def _llm_type(self) -> str:
        return "fake-runtime-lg-agent-model"


def test_max_turns_none_means_no_call_limit_middleware_and_no_cap() -> None:
    """The default (no max_turns passed) must not cap anything -- a model
    that keeps "talking" for more calls than any old default max_turns
    would have allowed should still run to completion. Guards against a
    regression that makes max_turns=None accidentally still install
    ModelCallLimitMiddleware with some fallback limit."""
    model = _FakeModel(responses=[AIMessage(content="ok")])
    agent = build_langgraph_agent(model, [], "be helpful", checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}
    result = agent.invoke({"messages": [HumanMessage(content="hi")]}, config=config)
    assert result["messages"][-1].content == "ok"


def test_max_turns_ends_the_run_after_the_configured_number_of_model_calls() -> None:
    """ModelCallLimitMiddleware(run_limit=max_turns, exit_behavior="end")
    must stop a runaway tool-calling loop -- a model that always proposes
    another tool call would otherwise never stop on its own. With
    max_turns=2 and a fake model given only two scripted tool-call
    responses, a third model call (which the uncapped loop would make,
    since the model always asks for another tool call) would IndexError --
    proving the cap actually held, not just that build_langgraph_agent
    accepted the parameter. The run must end with ModelCallLimitMiddleware's
    own injected "limit exceeded" message rather than raising."""

    def noop_tool() -> str:
        """A tool that does nothing."""
        return "done"

    # Two objects: one reused would get the same message id and replace
    # itself in the history the cap counts.
    model = _FakeModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "noop_tool", "args": {}, "id": f"c{i}"}])
            for i in range(2)
        ]
    )
    agent = build_langgraph_agent(
        model, [noop_tool], "be helpful", checkpointer=InMemorySaver(), max_turns=2
    )
    config = {"configurable": {"thread_id": "t1"}}
    result = agent.invoke({"messages": [HumanMessage(content="loop forever")]}, config=config)
    assert model.i == 2
    assert result["messages"][-1].content == "Model call limits exceeded: turn limit (2/2)"


def test_max_turns_holds_across_approvals() -> None:
    """LangChain's own run limit starts again at 0 on every resume after an
    approval, so a turn that paused on each step ran 61 model calls under a
    cap of 20; the count now comes from the history."""
    from langgraph.types import Command

    def noop_tool() -> str:
        """A tool that does nothing."""
        return "done"

    model = _FakeModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "noop_tool", "args": {}, "id": f"c{i}"}])
            for i in range(3)
        ]
    )
    agent = build_langgraph_agent(
        model,
        [noop_tool],
        "be helpful",
        checkpointer=InMemorySaver(),
        extra_interrupt_tool_names=["noop_tool"],
        max_turns=2,
    )
    config = {"configurable": {"thread_id": "t1"}}
    agent.invoke({"messages": [HumanMessage(content="loop forever")]}, config=config)
    for _ in range(3):
        tasks = agent.get_state(config).tasks
        if not tasks or not tasks[0].interrupts:
            break
        interrupt = tasks[0].interrupts[0]
        decisions = [{"type": "approve"} for _ in interrupt.value.get("action_requests", [])]
        result = agent.invoke(
            Command(resume={interrupt.id: {"decisions": decisions}}), config=config
        )

    assert model.i == 2
    assert result["messages"][-1].content == "Model call limits exceeded: turn limit (2/2)"


def test_a_turn_starts_at_the_users_message_not_at_a_compact_summary() -> None:
    from coscribe.runtime_lg.agent import model_calls_this_turn

    summary = HumanMessage("summary", additional_kwargs={"lc_source": "summarization"})
    history = [HumanMessage("first"), AIMessage("a"), HumanMessage("second"), AIMessage("b")]

    assert model_calls_this_turn(history) == 1
    assert model_calls_this_turn([*history, summary, AIMessage("c")]) == 2
    assert model_calls_this_turn([*history, HumanMessage("third")]) == 0


def test_a_note_added_mid_turn_neither_starts_a_turn_nor_reads_as_one() -> None:
    """Counted as a new request, each note would hand the turn a fresh
    budget; shown as one, it would split the turn in the log."""
    from coscribe.runtime_lg.agent import model_calls_this_turn
    from coscribe.runtime_lg.messages import serialize_history_for_ws_lg, steer_message

    history = [HumanMessage("go"), AIMessage("a"), steer_message("also b"), AIMessage("b")]

    assert model_calls_this_turn(history) == 2
    assert serialize_history_for_ws_lg(history)[2] == {"kind": "steer", "text": "also b"}


def test_auto_compact_tokens_collapses_a_long_thread_once_past_the_keep_floor() -> None:
    """SummarizationMiddleware's own default keep=("messages", 20) means a
    thread shorter than 20 messages is never touched even once the token
    trigger fires (cutoff_index <= 0 -> before_model returns None) -- this
    drives 22 user/assistant turns (comfortably over that floor) through
    one thread with auto_compact_tokens=1 (the lowest legal trigger --
    SummarizationMiddleware rejects <= 0), and asserts the checkpointed
    message list has genuinely collapsed: real proof
    RemoveMessage(id=REMOVE_ALL_MESSAGES) + a summary HumanMessage +
    preserved tail actually replaced the history, not just that
    build_langgraph_agent accepted the parameter without erroring."""
    replies = [AIMessage(content=f"reply {i}") for i in range(22)]
    # SummarizationMiddleware's own summary-generation call
    # (self.model.invoke(...) inside _create_summary) also draws from this
    # same fake model -- generous extra headroom so a real summarization
    # call never IndexErrors regardless of exactly which turn first crosses
    # the near-zero token trigger.
    replies.extend(AIMessage(content=f"summary {i}") for i in range(22))
    model = _FakeModel(responses=replies)
    checkpointer = InMemorySaver()
    agent = build_langgraph_agent(
        model, [], "be helpful", checkpointer=checkpointer, auto_compact_tokens=1
    )
    config = {"configurable": {"thread_id": "t1"}}

    for i in range(22):
        agent.invoke({"messages": [HumanMessage(content=f"message {i}")]}, config=config)

    state = agent.get_state(config)
    messages = state.values["messages"]
    # 22 user + 22 assistant = 44 without summarization ever kicking in.
    assert len(messages) < 44
    assert any(msg.additional_kwargs.get("lc_source") == "summarization" for msg in messages)


def test_auto_compact_tokens_none_means_no_summarization_middleware() -> None:
    """The default (no auto_compact_tokens passed) must not summarize
    anything, even past the 20-message floor -- guards against a regression
    that makes the None case accidentally still install
    SummarizationMiddleware with some fallback trigger."""
    replies = [AIMessage(content=f"reply {i}") for i in range(22)]
    model = _FakeModel(responses=replies)
    agent = build_langgraph_agent(model, [], "be helpful", checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    for i in range(22):
        agent.invoke({"messages": [HumanMessage(content=f"message {i}")]}, config=config)

    state = agent.get_state(config)
    messages = state.values["messages"]
    assert len(messages) == 44


def _dummy_tool(x: str) -> str:
    """A minimal tool -- exists only to give the cache-control test a
    real tool definition to check for a cache_control tag on.

    Args:
        x: unused, just needs a real parameter for a real input_schema.
    """
    return x


async def test_anthropic_prompt_caching_middleware_tags_system_prompt_and_tools() -> None:
    """The actual, provider-specific half of ROADMAP.md's Phase 0 caching
    entry: AnthropicPromptCachingMiddleware must be wired in and genuinely
    fire for a real ChatAnthropic instance -- proving it beyond "the
    import doesn't error" needs a real request payload to inspect, so
    this mocks ChatAnthropic's own async SDK client (same "assert the
    exact request shape" level of verification the now-deleted
    providers/anthropic_provider.py's own test used, per ROADMAP.md) --
    not a real network call, no live key needed. If this stops firing (a
    langchain-anthropic upgrade renaming/removing the middleware, or
    build_langgraph_agent's own wiring regressing), this test fails
    instead of silently losing caching.

    Mocks `messages.with_raw_response.create`, not `messages.create` --
    langchain-anthropic's own `_sdk_compat._aparse` (see that module's
    docstring) now always goes through the raw-response variant and calls
    `.parse()` on whatever comes back, so a fake that returns an already-
    parsed Message directly (as this test used to) fails with
    `AttributeError: 'Message' object has no attribute 'parse'` on
    current langchain-anthropic. `_aparse` accepts a sync *or* awaitable
    `.parse()` (`anthropic<1` vs. `>=1`'s `AsyncAPIResponse`), so a plain
    sync one here is correct either way, not tied to whichever `anthropic`
    major version happens to be installed."""
    captured: dict[str, Any] = {}

    class _FakeRawResponse:
        def __init__(self, message: AnthropicMessage) -> None:
            self._message = message

        def parse(self) -> AnthropicMessage:
            return self._message

    async def fake_create(**kwargs: Any) -> _FakeRawResponse:
        captured.update(kwargs)
        return _FakeRawResponse(
            AnthropicMessage(
                id="msg_1",
                type="message",
                role="assistant",
                model="claude-3-5-sonnet-latest",
                content=[AnthropicTextBlock(type="text", text="hi there")],
                stop_reason="end_turn",
                stop_sequence=None,
                usage=AnthropicUsage(input_tokens=10, output_tokens=5),
            )
        )

    model = ChatAnthropic(
        model_name="claude-3-5-sonnet-latest",
        api_key=SecretStr("fake-key"),
        timeout=None,
        stop=None,
    )
    model._async_client.messages.with_raw_response.create = fake_create  # type: ignore[assignment]

    agent = build_langgraph_agent(
        model, [_dummy_tool], "You are a helpful assistant.", checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "t1"}}
    await agent.ainvoke({"messages": [HumanMessage(content="hello")]}, config=config)

    system = captured["system"]
    assert system[-1]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    tools = captured["tools"]
    assert tools[-1]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}


def test_non_anthropic_model_is_not_tagged_and_raises_no_warning() -> None:
    """A non-Anthropic model (Gemini is the default; a user can switch models
    on any thread) gets no AnthropicPromptCachingMiddleware, and no Python
    warning either."""
    model = _FakeModel(responses=[AIMessage(content="ok")])
    agent = build_langgraph_agent(model, [], "be helpful", checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = agent.invoke({"messages": [HumanMessage(content="hi")]}, config=config)

    assert result["messages"][-1].content == "ok"


def test_the_anthropic_sdk_is_not_imported_for_a_model_that_is_not_anthropic() -> None:
    """Importing it costs ~0.6s of every server start."""
    import subprocess
    import sys

    code = (
        "import sys; from langgraph.checkpoint.memory import InMemorySaver\n"
        "from langchain_core.language_models.fake_chat_models import FakeListChatModel\n"
        "from coscribe.runtime_lg.agent import build_langgraph_agent\n"
        "build_langgraph_agent(FakeListChatModel(responses=['x']), [], 'p',"
        " checkpointer=InMemorySaver())\n"
        "print('anthropic' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.stdout.strip() == "False", out.stderr


class _RecordingModel(_FakeModel):
    seen: list[list[BaseMessage]] = []

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def test_a_history_with_an_unanswered_tool_call_reaches_the_model_answered() -> None:
    """Providers reject a request whose tool call has no result after it
    with a 400. Whatever left one in a saved thread, the model must still
    get a valid history -- and the saved history stays as it was."""
    model = _RecordingModel(responses=[AIMessage(content="ok")], seen=[])
    agent = build_langgraph_agent(model, [], "be helpful", checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}
    calls = [
        {"name": "lookup", "args": {}, "id": "done"},
        {"name": "lookup", "args": {}, "id": "orphan"},
    ]
    agent.update_state(
        config,
        {
            "messages": [
                HumanMessage(content="go"),
                AIMessage(content="", tool_calls=calls),
                ToolMessage(content="found", tool_call_id="done", name="lookup"),
            ]
        },
        as_node="tools",
    )
    agent.invoke({"messages": [HumanMessage(content="continue")]}, config=config)

    sent = [m for m in model.seen[0] if not isinstance(m, SystemMessage)]
    assert [type(m).__name__ for m in sent] == [
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
        "ToolMessage",
        "HumanMessage",
    ]
    assert [m.tool_call_id for m in sent[2:4]] == ["done", "orphan"]
    saved = agent.get_state(config).values["messages"]
    assert [m.tool_call_id for m in saved if isinstance(m, ToolMessage)] == ["done"]


def test_a_file_open_in_another_program_is_explained_not_dumped_raw() -> None:
    from types import SimpleNamespace

    from coscribe.runtime_lg.agent import _tool_error_message

    request = SimpleNamespace(tool_call={"id": "c1", "name": "write_xlsx"})
    locked = PermissionError(13, "Permission denied", "C:\\Users\\a\\report.xlsx")
    guard = PermissionError("Path is outside the workspace: C:\\other\\x.xlsx")
    ordinary = ValueError("File does not exist: a.txt")

    assert "report.xlsx" in _tool_error_message(request, locked).content
    assert "open in another program" in _tool_error_message(request, locked).content
    # The workspace guard's own PermissionError (no errno) keeps its message.
    assert _tool_error_message(request, guard).content == str(guard)
    assert _tool_error_message(request, ordinary).content == str(ordinary)


def test_a_file_that_will_not_open_says_what_to_do_instead_of_a_library_error() -> None:
    import zipfile
    from types import SimpleNamespace

    from pptx.exc import PackageNotFoundError

    from coscribe.runtime_lg.agent import _tool_error_message

    def message(path: str, exc: Exception) -> str:
        request = SimpleNamespace(tool_call={"id": "c1", "name": "read_x", "args": {"path": path}})
        return str(_tool_error_message(request, exc).content)

    legacy = message("old.xls", zipfile.BadZipFile("File is not a zip file"))
    assert "convert_office_file" in legacy and "xlsx" in legacy
    assert "pptx" in message("deck.ppt", PackageNotFoundError("Package not found at 'deck.ppt'"))
    damaged = message("report.docx", zipfile.BadZipFile("File is not a zip file"))
    assert "damaged" in damaged and "convert_office_file" not in damaged
    other = ValueError("File does not exist: a.txt")
    assert message("a.txt", other) == "File does not exist: a.txt"

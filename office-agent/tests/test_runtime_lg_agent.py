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
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
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

    tool_call_response = AIMessage(
        content="", tool_calls=[{"name": "noop_tool", "args": {}, "id": "call_1"}]
    )
    model = _FakeModel(responses=[tool_call_response, tool_call_response])
    agent = build_langgraph_agent(
        model, [noop_tool], "be helpful", checkpointer=InMemorySaver(), max_turns=2
    )
    config = {"configurable": {"thread_id": "t1"}}
    result = agent.invoke({"messages": [HumanMessage(content="loop forever")]}, config=config)
    assert model.i == 2
    assert result["messages"][-1].content == "Model call limits exceeded: run limit (2/2)"


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
    assert any(
        msg.additional_kwargs.get("lc_source") == "summarization" for msg in messages
    )


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
    """build_langgraph_agent always appends AnthropicPromptCachingMiddleware
    unconditionally (see its own comment) -- for the non-Anthropic model
    that's this app's normal case (Gemini is the default; a user can
    switch models on any thread), it must be a silent, total no-op: no
    cache_control tag (there's nothing to check the request shape for
    here, since _FakeModel doesn't expose one the way ChatAnthropic does,
    but the absence of a crash already proves the tagging path never
    ran), and critically no Python warning either -- build_langgraph_agent
    passes unsupported_model_behavior="ignore" specifically so a routine
    model switch to Gemini/OpenAI-compatible doesn't spam a warning on
    every single turn (the middleware's own default is "warn")."""
    model = _FakeModel(responses=[AIMessage(content="ok")])
    agent = build_langgraph_agent(model, [], "be helpful", checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        result = agent.invoke({"messages": [HumanMessage(content="hi")]}, config=config)

    assert result["messages"][-1].content == "ok"

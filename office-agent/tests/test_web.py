# ruff: noqa: E402
"""Tests for coscribe's web UI (web/app.py + web/session.py), built on
runtime_lg -- see runtime_lg/README.md for the full migration history.
This file was tests/test_web_lg.py through Phase 3 of that migration,
developed alongside a test_web.py covering the original hand-rolled-
runtime app as a parallel track; that original suite was deleted and this
one promoted in its place in the web cutover (same WS message type/
ordering contract the old suite asserted, now the only contract there is
-- see runtime_lg/README.md's "web cutover" section).

`pytest.importorskip("langgraph", ...)` below is a defensive guard, not a
real skip condition in practice: langchain/langgraph are base dependencies
of this project now (no longer a separate, hard-to-combine extra), so this
only skips collection in an environment that hasn't run the normal
`pip install -e ".[dev,web]"` at all.
"""

import asyncio
import contextlib
import json
import os
import shutil
import socket
import stat
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from docx import Document
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from openpyxl import Workbook, load_workbook
from pptx import Presentation
from pptx.util import Inches

from coscribe.config import Settings
from coscribe.runtime import secrets as secrets_module
from coscribe.runtime_lg.audit import AuditLog
from coscribe.tools.presentations import PresentationToolkit
from coscribe.tools.subagent_tasks import SubAgentTaskStore
from coscribe.web.app import ScriptEnvPackageInstall, create_app_lg


class FakeToolCallingChatModel(BaseChatModel):
    """A minimal scripted chat model good enough to drive
    build_langgraph_agent's create_agent + HumanInTheLoopMiddleware: cycles
    through `responses` in order, and always streams each one as a single
    AIMessageChunk (langchain_core's own FakeMessagesListChatModel doesn't
    implement `_stream` at all, which would make every message arrive as a
    plain AIMessage over agent.astream(..., stream_mode=["messages"]) --
    session.py's _stream_turn only forwards AIMessageChunk, matching what
    every real provider integration actually streams)."""

    responses: list[AIMessage]
    i: int = 0
    # Every call's messages, in order -- a few tests need to inspect what
    # actually got sent (e.g. confirming a per-turn note like the date
    # note is really in the human message, not just trusted to be there).
    # Safe as a plain mutable default here (unlike a bare dataclass):
    # pydantic BaseModel deep-copies list/dict defaults per instance.
    received: list[list[BaseMessage]] = []

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.received.append(list(messages))
        message = self.responses[self.i]
        self.i += 1
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.received.append(list(messages))
        message = self.responses[self.i]
        self.i += 1
        chunk = AIMessageChunk(
            content=message.content or "",
            tool_calls=message.tool_calls,
            id=message.id,
            usage_metadata=message.usage_metadata,
        )
        yield ChatGenerationChunk(message=chunk)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling-chat-model"


def _tool_call(call_id: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "args": args, "id": call_id}


class HangingChatModel(BaseChatModel):
    """Simulates a provider SDK stuck inside its own internal call (e.g.
    langchain_google_genai's retry/backoff loop on a quota error) with no
    chunk ever yielded -- the exact shape of the real, user-reported "Stop
    doesn't stop" bug. `_astream` genuinely suspends on `asyncio.sleep`
    (a real await point, unlike a blocking call), so a cancelled task can
    actually be interrupted there, the same way a real stuck network call
    can be. Sets `started` (a plain threading.Event, safe to wait on from
    the test's own thread since TestClient's websocket runs the app on a
    separate thread with its own event loop) right before suspending, so
    a test can wait until the model call has genuinely begun before it
    sends "stop" -- otherwise it would race request_stop() against
    _current_turn_task even being assigned yet."""

    started: Any = None  # threading.Event, plain Any field for pydantic

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("only the async streaming path is exercised by this test")

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.started.set()
        await asyncio.sleep(999)
        yield ChatGenerationChunk(message=AIMessageChunk(content="never reached"))

    @property
    def _llm_type(self) -> str:
        return "hanging-chat-model"


class GrpcMetadataOverflowThenSuccessModel(BaseChatModel):
    """Simulates the real langchain_google_genai gRPC-metadata-overflow
    failure mode (see session.py's own _GRPC_METADATA_OVERFLOW_SIGNATURE
    comment): raises that exact error signature on its first call, then
    behaves like a normal FakeToolCallingChatModel from the second call
    onward -- standing in for "a fresh client/channel doesn't have the
    problem," since _client_lg's resolve_chat_model stub returns this
    same instance again on the retry (a real fresh ChatGoogleGenerativeAI
    would be a genuinely different object, but what this test needs to
    prove is that session.py's retry loop fires and a client that works
    on the second attempt succeeds, not that this fake actually
    reconnects anything)."""

    responses: list[AIMessage]
    i: int = 0
    calls: int = 0

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError("only the async streaming path is exercised by this test")

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        self.calls += 1
        if self.calls == 1:
            raise Exception(
                "429 Stream removed (received metadata size exceeds soft limit "
                "(15093 vs. 8192); grpc-status:44B grpc-message:15049B"
            )
        message = self.responses[self.i]
        self.i += 1
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=message.content or "", tool_calls=message.tool_calls)
        )

    @property
    def _llm_type(self) -> str:
        return "grpc-metadata-overflow-then-success-model"


class ConcurrentSpawnFakeModel(BaseChatModel):
    """Routes each call by inspecting the conversation's own content rather
    than an index counter -- needed only for the concurrent-spawn_agent
    test below. Two concurrent spawn_agent calls run their own sub-agent's
    model turn on genuinely separate OS threads sharing this one model
    instance (LangGraph's Pregel executor runs same-superstep tool-node
    tasks concurrently -- verified live against real Gemini, see
    runtime_lg/README.md), so FakeToolCallingChatModel's shared `self.i`
    counter would race and nondeterministically assign a canned response
    to the wrong sub-agent. Content-routing sidesteps the race entirely:
    each thread only ever sees messages containing its own marker text."""

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self

    def _respond(self, messages: list[BaseMessage]) -> AIMessage:
        text = " ".join(str(getattr(m, "content", "")) for m in messages)
        has_tool_result = any(isinstance(m, ToolMessage) for m in messages)
        if "spawn_agent TWICE" in text and not has_tool_result:
            return AIMessage(
                content="",
                tool_calls=[
                    _tool_call(
                        "call_spawn_a",
                        "spawn_agent",
                        {
                            "description": "write a.txt",
                            "prompt": "write a.txt containing exactly: AAA marker",
                            "tool_names": "write_file",
                        },
                    ),
                    _tool_call(
                        "call_spawn_b",
                        "spawn_agent",
                        {
                            "description": "write b.txt",
                            "prompt": "write b.txt containing exactly: BBB marker",
                            "tool_names": "write_file",
                        },
                    ),
                ],
            )
        if "AAA marker" in text and not has_tool_result:
            call = _tool_call("call_a", "write_file", {"path": "a.txt", "content": "AAA"})
            return AIMessage(content="", tool_calls=[call])
        if "BBB marker" in text and not has_tool_result:
            call = _tool_call("call_b", "write_file", {"path": "b.txt", "content": "BBB"})
            return AIMessage(content="", tool_calls=[call])
        if "AAA marker" in text:
            return AIMessage(content="sub-agent A done")
        if "BBB marker" in text:
            return AIMessage(content="sub-agent B done")
        return AIMessage(content="parent done")

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._respond(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        message = self._respond(messages)
        chunk = AIMessageChunk(
            content=message.content or "", tool_calls=message.tool_calls, id=message.id
        )
        yield ChatGenerationChunk(message=chunk)

    @property
    def _llm_type(self) -> str:
        return "concurrent-spawn-fake-model"


class _FakeContextWindowClient:
    """Stand-in for LLMClient, used by ChatSessionLG.send_state only for its
    get_context_window() heuristic -- the real LLMClient would try to
    resolve "fake:model" as a live aisuite provider and fail.

    Also stands in for app.py's context_window_client in the
    /api/providers tests below -- register_custom_provider/
    deregister_custom_provider/invalidate_provider are plain in-memory
    bookkeeping on the real LLMClient too (no live provider calls), so a
    minimal fake here is enough to exercise those endpoints without a real
    LLMClient's aisuite-backed construction."""

    def get_context_window(self, model: str) -> int:
        return 128_000

    def register_custom_provider(self, name: str, config: dict[str, str]) -> None:
        pass

    def deregister_custom_provider(self, name: str) -> None:
        pass

    def invalidate_provider(self, name: str) -> None:
        pass


class _FakeKeyring:
    """Same in-memory stand-in as test_secrets.py's identical class -- this
    sandbox has no real OS keychain backend (confirmed live:
    `keyring.backends.fail.Keyring`), so the existing provider/MCP tests
    above naturally exercise runtime/secrets.py's plaintext fallback path.
    These tests mock a working keyring instead, to also cover the
    keyring-*available* path deterministically."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        import keyring.errors

        self.errors = keyring.errors

    def set_password(self, service: str, ref: str, value: str) -> None:
        self.store[(service, ref)] = value

    def get_password(self, service: str, ref: str) -> str | None:
        return self.store.get((service, ref))

    def delete_password(self, service: str, ref: str) -> None:
        self.store.pop((service, ref), None)


def _install_fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_module, "keyring", fake)
    return fake


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "default_model": "fake:model",
        "workspace_root": tmp_path / "workspace",
        "state_dir": tmp_path / "state",
        "skills_dir": tmp_path / "skills",
        "memory_path": tmp_path / "MEMORY.md",
        # Its extra model call would take scripted responses meant for
        # the test's own turns.
        "auto_title_threads": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[call-arg, arg-type]


@contextlib.contextmanager
def _client_lg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_model: FakeToolCallingChatModel,
    **settings_overrides: object,
) -> Any:
    monkeypatch.setattr(
        "coscribe.web.session.resolve_chat_model",
        lambda model, custom_providers=None: fake_model,
    )
    # No connect_mcp_tools_lg monkeypatch needed here -- it's only ever
    # awaited inside the lifespan when settings.mcp_config_path is set, and
    # no test *starts* with that set (see _settings' defaults above) --
    # only the /api/mcp/servers tests below set it dynamically via POST,
    # which goes through connect_one_mcp_server_lg instead (stubbed here,
    # same "saved but didn't connect live by default" contract
    # test_web.py's equivalent connect_one_mcp_server stub uses -- a
    # specific test overrides this when it needs to prove tools actually
    # got spliced in).
    async def _fake_connect_one_mcp_server_lg(
        name: str, config: Any
    ) -> tuple[list[Any], None, None]:
        return [], None, None

    monkeypatch.setattr(
        "coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", _fake_connect_one_mcp_server_lg
    )
    monkeypatch.setattr(
        "coscribe.web.app.LLMClient",
        lambda custom_providers=None: _FakeContextWindowClient(),
    )
    app = create_app_lg(_settings(tmp_path, **settings_overrides))
    # Unlike test_web.py's plain TestClient(app), this app's lifespan does
    # real setup (opening the AsyncSqliteSaver checkpointer) that only runs
    # when the client is used as a context manager -- see starlette's
    # TestClient.__enter__, which is the only path that starts the ASGI
    # lifespan protocol at all.
    with TestClient(app) as client:
        yield client


def _receive_until(ws: Any, target_type: str) -> list[dict[str, Any]]:
    messages = []
    while True:
        message = ws.receive_json()
        messages.append(message)
        if message["type"] == target_type:
            return messages


def test_chat_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t1") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history
            assert state["type"] == "state"
            assert state["plan_mode"] is False
            assert state["accept_edits"] is False
            assert state["model"] == "fake:model"
            assert isinstance(state["context_window"], int)

            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "hi there!"
    assert messages[-1]["type"] == "tasks_changed"


def test_usage_event_sent_when_the_model_reports_usage_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported gap: the WS "usage" event
    (drives the frontend's context-usage bar) was deliberately never sent,
    because an earlier check against streaming Gemini found
    AIMessageChunk.usage_metadata always came back all zeros. Re-checked
    live against the currently pinned langchain-google-genai and found that
    finding stale -- an upstream fix means real per-chunk usage now flows
    through this exact astream(stream_mode=["messages"]) path. See
    runtime_lg/README.md's "usage bar" section for the live verification
    (including a real tool call and a second turn's usage correctly
    growing) this unit test can't reach on its own (FakeToolCallingChatModel
    has no live API to hit)."""
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="hi there!",
                usage_metadata=UsageMetadata(input_tokens=10, output_tokens=5, total_tokens=15),
            )
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_usage") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    usage_message = next(m for m in messages if m["type"] == "usage")
    assert usage_message == {"type": "usage", "total_tokens": 15}


def test_usage_events_are_live_per_response_not_summed_across_a_tool_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool-calling turn involves two separate model responses (propose
    the call, then respond to its result) -- each one's own usage_metadata
    already reflects the *cumulative* context size at that point (every
    provider reports "tokens in the whole prompt this call sent" as
    input_tokens, not a delta since the last call), so summing both
    responses together would double-count: the sequence below must never
    contain 145 (60 + 85).

    Also the live-per-segment regression test for RunStatus's running
    turn timer: _stream_turn sends "usage" itself at each ToolMessage
    boundary now, not only once the whole turn is over, so the first
    response's total (60) must reach the client *before* that call's own
    "tool_result" event -- proof it's live, not batched to the end. The
    final 85 shows up twice (once from _stream_turn's own end-of-call
    send, once more from this handler's existing post-turn send) --
    harmless, same value both times, not worth suppressing the second
    one for."""
    call = _tool_call("call_1", "list_files", {})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[call],
                usage_metadata=UsageMetadata(input_tokens=50, output_tokens=10, total_tokens=60),
            ),
            AIMessage(
                content="done",
                usage_metadata=UsageMetadata(input_tokens=80, output_tokens=5, total_tokens=85),
            ),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_usage_tool") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "list files"})
            messages = _receive_until(ws, "tasks_changed")

    usage_totals = [m["total_tokens"] for m in messages if m["type"] == "usage"]
    assert usage_totals == [60, 85, 85]

    usage_60 = {"type": "usage", "total_tokens": 60}
    usage_60_index = next(i for i, m in enumerate(messages) if m == usage_60)
    tool_result_index = next(i for i, m in enumerate(messages) if m["type"] == "tool_result")
    assert usage_60_index < tool_result_index


def test_usage_event_includes_cache_stats_when_the_provider_reports_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real, user-reported gap: the token counter only ever showed a
    running total_tokens, with no way to tell whether prompt caching was
    actually reducing anything -- a long, tool-heavy conversation looks
    identical either way from that one number alone. langchain-core's
    standard `input_token_details.cache_read` field (populated for
    Anthropic's own explicit cache_control breakpoints, and, unprompted
    by any coscribe code, by langchain_openai for any OpenAI-compatible
    provider that reports its own `prompt_tokens_details.cached_tokens`
    -- GLM's documented "implicit caching" is exactly this shape) is now
    surfaced in the "usage" event as cache_read_tokens/input_tokens/
    cache_hit_rate. See test_usage_event_sent_when_the_model_reports_
    usage_metadata right above for the *absence* case (no
    input_token_details at all) -- this proves the *presence* case,
    including the exact hit-rate arithmetic."""
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="hi there!",
                usage_metadata=UsageMetadata(
                    input_tokens=1000,
                    output_tokens=50,
                    total_tokens=1050,
                    input_token_details={"cache_read": 800},
                ),
            )
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_usage_cache") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    usage_message = next(m for m in messages if m["type"] == "usage")
    assert usage_message == {
        "type": "usage",
        "total_tokens": 1050,
        "cache_read_tokens": 800,
        "input_tokens": 1000,
        "cache_hit_rate": 0.8,
    }


def test_no_usage_event_when_the_model_never_reports_usage_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_usage_none") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    assert not any(m["type"] == "usage" for m in messages)


def test_max_turns_ends_the_run_gracefully_instead_of_looping_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings.max_turns used to have no runtime_lg equivalent at all (see
    config.py's docstring / runtime_lg/README.md's "audit + delete old
    runtime" section) -- a model that keeps calling tools forever would
    never stop. Wired via ModelCallLimitMiddleware(run_limit=max_turns,
    exit_behavior="end"): with max_turns=2 and a model that always proposes
    a tool call, the model must only ever be invoked twice -- a third call
    would IndexError against this fake's two-item `responses` list, proving
    the cap didn't hold (pytest would report that error, not a clean
    assertion failure, but it would still fail the test either way).

    Also exercises a real bug this test caught along the way: the
    middleware's own injected "limit exceeded" message arrives through
    astream(stream_mode=["messages"]) as one complete AIMessage, not an
    AIMessageChunk (it's added directly by a before_model hook, never
    actually streamed from a model) -- _stream_turn originally only handled
    AIMessageChunk/ToolMessage, so this text was silently dropped and the
    turn ended with a blank "[no reply -- ...]" instead of telling the user
    why it stopped. Fixed by adding an AIMessage branch to _stream_turn."""
    call = _tool_call("call_1", "list_files", {})
    tool_call_response = AIMessage(content="", tool_calls=[call])
    fake_model = FakeToolCallingChatModel(responses=[tool_call_response, tool_call_response])
    with _client_lg(tmp_path, monkeypatch, fake_model, max_turns=2) as client:
        with client.websocket_connect("/ws/t_max_turns") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "loop forever"})
            messages = _receive_until(ws, "tasks_changed")

    assert not any(m["type"] == "error" for m in messages)
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "Model call limits exceeded: run limit (2/2)"


def test_agent_delta_events_stream_before_the_final_agent_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t10") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "agent_delta" in types
    assert types.index("agent_delta") < types.index("agent_message")
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "hi there!"


def test_write_file_requires_approval_and_executes_when_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["tool_name"] == "write_file"
            assert approval["arguments"] == {"path": "note.txt", "content": "hi"}

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "tool_result" in types
    assert types.index("tool_result") < types.index("agent_message")
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "done"
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].thread_id == "t2"
    assert entries[0].tool_name == "write_file"
    assert entries[0].decision == "approve"
    assert entries[0].reason == "human"


def test_narration_before_a_gated_tool_call_does_not_get_glued_onto_the_final_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real bug, found live against DeepSeek/GLM (both narrate before a
    gated tool call, unlike Gemini/Anthropic in this app's usual testing):
    the pre-tool narration text streamed live as its own "agent_delta" run,
    then got concatenated with no separator onto the *front* of the final
    "agent_message" -- "I'll write it now.Done, I wrote the file." glued
    together, because session.py's turn handler used to join every model
    response of a turn into one string. Fixed by having _stream_turn/
    _resolve_pending_approvals each return only their own *last* response's
    text -- see their docstrings."""
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="I'll write it now.", tool_calls=[call]),
            AIMessage(content="Done, I wrote the file."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t2b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})

            delta = ws.receive_json()
            assert delta == {"type": "agent_delta", "text": "I'll write it now."}
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            messages = _receive_until(ws, "tasks_changed")

    deltas = [m["text"] for m in messages if m["type"] == "agent_delta"]
    assert deltas == ["Done, I wrote the file."]
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    # The bug's exact shape: "I'll write it now.Done, I wrote the file."
    assert agent_message["text"] == "Done, I wrote the file."


def test_narration_before_an_ungated_tool_call_does_not_get_glued_onto_the_final_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same underlying bug as
    test_narration_before_a_gated_tool_call_does_not_get_glued_onto_the_
    final_reply, but for a tool that never asks for approval -- both
    model responses stream within a *single* _stream_turn call here (no
    approval_required event splits them), so this exercises that
    method's own text_parts.clear() reset at the ToolMessage boundary,
    not _resolve_pending_approvals's fix."""
    call = _tool_call("call_1", "read_file", {"path": "note.txt"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="Let me check that file.", tool_calls=[call]),
            AIMessage(content="It says hello."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_ungated_narration") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
            (tmp_path / "workspace" / "note.txt").write_text("hello")
            ws.send_json({"type": "user_message", "text": "read note.txt"})
            messages = _receive_until(ws, "tasks_changed")

    deltas = [m["text"] for m in messages if m["type"] == "agent_delta"]
    assert deltas == ["Let me check that file.", "It says hello."]
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    # The bug's exact shape: "Let me check that file.It says hello."
    assert agent_message["text"] == "It says hello."


def _libreoffice_and_poppler_actually_work() -> bool:
    """Same probe technique as test_presentations_tool.py's identical
    helper (not imported from there -- each test file in this project
    stays self-contained): `shutil.which` alone can't tell a genuinely
    broken soffice/pdftoppm install from a working one, so this actually
    tries a trivial conversion in a throwaway temp dir."""
    if shutil.which("pdftoppm") is None:
        return False
    probe_dir = Path(tempfile.mkdtemp(prefix="coscribe_lo_probe_"))
    try:
        result = PresentationToolkit(probe_dir).write_pptx(
            path="probe.pptx", content="# Probe\n- hi"
        )
        return result["qa_skipped_reason"] is None
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


def _write_test_deck(workspace: Path, name: str = "deck.pptx") -> None:
    """A one-slide deck with a single plain (unfilled) textbox -- enough
    for the approval-preview tests below to have something real to
    diff a fill-color edit against."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(2), Inches(1))
    box.text_frame.text = "hello"
    (workspace).mkdir(parents=True, exist_ok=True)
    prs.save(str(workspace / name))


@pytest.mark.real_libreoffice
@pytest.mark.skipif(
    not _libreoffice_and_poppler_actually_work(),
    reason="LibreOffice or poppler-utils not installed/functional in this environment",
)
def test_approval_required_carries_a_before_after_preview_for_a_pptx_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """web/session.py's _build_document_edit_preview dry-runs the exact same
    edit_pptx_shape call against a throwaway copy of the deck and renders
    both states, so the approval card can show what the edit will
    actually produce instead of a raw JSON arguments dump (see
    ROADMAP.md's approval-preview entry). End-to-end through a real WS
    connection and a real LibreOffice/poppler conversion -- not mocked."""
    _write_test_deck(tmp_path / "workspace")
    call = _tool_call(
        "call_1",
        "edit_pptx_shape",
        {"path": "deck.pptx", "slide": 1, "shape_index": 0, "fill_color": "38BDF8"},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_pptx_preview") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "recolor that shape"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["tool_name"] == "edit_pptx_shape"
            before_name = approval["before_preview"]
            after_name = approval["after_preview"]
            assert before_name is not None
            assert after_name is not None
            assert before_name != after_name

            # The dry run must never touch the real file -- still
            # un-filled at this point, approval hasn't happened yet.
            real_deck = Presentation(str(tmp_path / "workspace" / "deck.pptx"))
            assert real_deck.slides[0].shapes[0].fill.type is None or str(
                real_deck.slides[0].shapes[0].fill.type
            ).startswith("BACKGROUND")

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

    previews_dir = tmp_path / "state" / "previews"
    assert (previews_dir / before_name).is_file()
    assert (previews_dir / after_name).is_file()


def test_approval_preview_is_absent_for_a_non_pptx_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """write_file has nothing to do with PresentationToolkit at all --
    _build_document_edit_preview must fall through to (None, None) instantly,
    not attempt (and fail at) treating its arguments as a pptx edit. No
    LibreOffice needed for this one, so it isn't gated behind the
    real_libreoffice marker."""
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_no_preview") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["before_preview"] is None
            assert approval["after_preview"] is None

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": False})
            _receive_until(ws, "agent_message")


def test_approval_preview_is_absent_when_the_target_file_does_not_exist_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """edit_pptx_shape on a path that doesn't exist yet has no "before"
    to show -- _build_document_edit_preview must recognize that up front
    (real_path.is_file() is False) and return (None, None), not attempt a
    dry run that would just fail. No LibreOffice needed: this returns
    before ever shelling out to soffice."""
    call = _tool_call(
        "call_1",
        "edit_pptx_shape",
        {"path": "missing.pptx", "slide": 1, "shape_index": 0, "fill_color": "38BDF8"},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_missing_preview") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "recolor that shape"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["before_preview"] is None
            assert approval["after_preview"] is None

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": False})
            _receive_until(ws, "agent_message")


@pytest.mark.real_libreoffice
@pytest.mark.skipif(
    not _libreoffice_and_poppler_actually_work(),
    reason="LibreOffice or poppler-utils not installed/functional in this environment",
)
def test_approval_preview_covers_write_docx_overwriting_an_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_build_document_edit_preview's dispatch table covers docx too,
    dispatched via DocumentToolkit -- write_docx regenerates the *whole*
    document from markdown-like `content`, so calling it against an
    already-existing file (this scenario: revising a one-paragraph status
    report) is exactly the "before/after" case worth previewing. A fresh
    write_docx with no existing target still correctly gets no preview
    (nothing to diff against) -- covered separately below, not repeated
    here."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    original = Document()
    original.add_paragraph("Status: on track.")
    original.save(str(workspace / "status.docx"))

    call = _tool_call(
        "call_1",
        "write_docx",
        {
            "path": "status.docx",
            "content": "Status: **delayed** -- see risks below.",
            "overwrite": True,
        },
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_docx_preview") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "update the status report"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["tool_name"] == "write_docx"
            before_name = approval["before_preview"]
            after_name = approval["after_preview"]
            assert before_name is not None
            assert after_name is not None
            assert before_name != after_name

            # Untouched until approved.
            real_doc = Document(str(workspace / "status.docx"))
            assert real_doc.paragraphs[0].text == "Status: on track."

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

    previews_dir = tmp_path / "state" / "previews"
    assert (previews_dir / before_name).is_file()
    assert (previews_dir / after_name).is_file()
    updated_doc = Document(str(workspace / "status.docx"))
    assert "delayed" in updated_doc.paragraphs[0].text


@pytest.mark.real_libreoffice
@pytest.mark.skipif(
    not _libreoffice_and_poppler_actually_work(),
    reason="LibreOffice or poppler-utils not installed/functional in this environment",
)
def test_approval_preview_covers_format_xlsx_cells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_build_document_edit_preview's dispatch table covers xlsx too, via
    SpreadsheetToolkit -- format_xlsx_cells never creates a file (only
    READ_check's the target), so real_path.is_file() being required is
    what makes this safe to dry-run unconditionally. Scenario: bolding
    and red-coloring a budget overage cell in an existing sheet."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Budget"
    sheet["A1"] = "Category"
    sheet["B1"] = "Spent"
    sheet["A2"] = "Marketing"
    sheet["B2"] = 15200
    workbook.save(str(workspace / "budget.xlsx"))

    call = _tool_call(
        "call_1",
        "format_xlsx_cells",
        {
            "path": "budget.xlsx",
            "sheet_name": "Budget",
            "cell_range": "B2",
            "font_color": "FF0000",
            "bold": True,
        },
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_xlsx_preview") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "flag the marketing overage in red bold"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["tool_name"] == "format_xlsx_cells"
            before_name = approval["before_preview"]
            after_name = approval["after_preview"]
            assert before_name is not None
            assert after_name is not None
            assert before_name != after_name

            real_workbook = load_workbook(str(workspace / "budget.xlsx"))
            assert real_workbook["Budget"]["B2"].font.bold is not True

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

    previews_dir = tmp_path / "state" / "previews"
    assert (previews_dir / before_name).is_file()
    assert (previews_dir / after_name).is_file()
    updated_workbook = load_workbook(str(workspace / "budget.xlsx"))
    assert updated_workbook["Budget"]["B2"].font.bold is True


def test_approval_preview_is_absent_for_a_brand_new_write_docx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """write_docx creating a file that doesn't exist yet has no "before"
    -- same "nothing to diff against" reasoning fill_pptx_template gets,
    now confirmed for docx's own create-vs-overwrite ambiguity (write_docx
    is *both* a create and an overwrite tool depending on whether the
    target already exists). No LibreOffice needed -- this returns before
    ever shelling out to soffice."""
    call = _tool_call(
        "call_1",
        "write_docx",
        {"path": "new_report.docx", "content": "Hello.", "overwrite": True},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_new_docx") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write a new report"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["before_preview"] is None
            assert approval["after_preview"] is None

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")


def test_approval_preview_leaves_the_real_file_untouched_on_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Denying an edit_pptx_shape call must leave the real file exactly as
    it was -- true by construction for the real *execution* (a denied
    HumanInTheLoopMiddleware decision never runs the tool at all), but
    this is specifically about the *preview* mechanism: confirms the dry
    run itself (which runs regardless of what the human eventually
    decides, since the preview is built before the approval prompt is
    even sent) never wrote through to the real path. No LibreOffice
    needed for the assertion that matters here (the file bytes are
    unchanged); the preview images themselves are covered elsewhere."""
    _write_test_deck(tmp_path / "workspace")
    original_bytes = (tmp_path / "workspace" / "deck.pptx").read_bytes()
    call = _tool_call(
        "call_1",
        "edit_pptx_shape",
        {"path": "deck.pptx", "slide": 1, "shape_index": 0, "fill_color": "38BDF8"},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="denied, ok")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_deny_preview") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "recolor that shape"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": False})
            _receive_until(ws, "agent_message")

    assert (tmp_path / "workspace" / "deck.pptx").read_bytes() == original_bytes


@pytest.mark.real_libreoffice
@pytest.mark.skipif(
    not _libreoffice_and_poppler_actually_work(),
    reason="LibreOffice or poppler-utils not installed/functional in this environment",
)
def test_approval_preview_resolves_a_file_in_an_extra_writable_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_build_document_edit_preview builds its own WorkspaceScope from
    self.settings.extra_readable_dirs/extra_writable_dirs, the same
    settings the real session's own tools use -- not just the plain
    workspace root. Confirms it actually resolves and renders a file
    that only exists via extra_writable_dirs (a directory outside the
    main workspace), the "file system is the core of this feature" case
    worth nailing explicitly rather than trusting by inspection alone --
    a resolve() bug here would silently degrade to (None, None) instead
    of erroring, so only a real render (non-None previews) proves it
    actually reached the file."""
    extra_dir = tmp_path / "shared_drive"
    _write_test_deck(extra_dir, name="external.pptx")
    call = _tool_call(
        "call_1",
        "edit_pptx_shape",
        {
            "path": str(extra_dir / "external.pptx"),
            "slide": 1,
            "shape_index": 0,
            "fill_color": "38BDF8",
        },
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(
        tmp_path, monkeypatch, fake_model, extra_writable_dirs=[extra_dir]
    ) as client:
        with client.websocket_connect("/ws/t_extra_writable") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "recolor that shape"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["before_preview"] is not None
            assert approval["after_preview"] is not None

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": False})
            _receive_until(ws, "agent_message")


def test_edit_file_batch_tool_works_end_to_end_through_the_agent_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real, live verification that edit_file_batch's `edits` string
    argument round-trips correctly through the actual LangGraph/
    LangChain tool-schema machinery (build_langgraph_agent -> a real
    StructuredTool, not just this function called directly in Python
    the way test_files_tool.py's unit tests do)."""
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
    (tmp_path / "workspace" / "a.txt").write_text("hello world\nsecond line\n", encoding="utf-8")
    call = _tool_call(
        "call_1",
        "edit_file_batch",
        {
            "path": "a.txt",
            "edits": "world\n---\nthere\n---\nsecond line\n---\nSECOND LINE",
        },
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_batch_edit") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "make both edits"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            assert approval["tool_name"] == "edit_file_batch"
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            messages = _receive_until(ws, "tasks_changed")

    tool_result = next(m for m in messages if m["type"] == "tool_result")
    assert "2" in json.dumps(tool_result)  # edits_applied
    assert (tmp_path / "workspace" / "a.txt").read_text() == "hello there\nSECOND LINE\n"


def test_ask_user_question_sends_question_required_and_feeds_answer_back_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call(
        "call_1",
        "ask_user_question",
        {
            "question": "What kind of plan?",
            "header": "Plan type",
            "options": "Travel\nStudy\nFitness",
            "multi_select": False,
        },
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "help me plan something"})

            question = ws.receive_json()
            assert question["type"] == "question_required"
            assert question["question"] == "What kind of plan?"
            assert question["header"] == "Plan type"
            assert question["options"] == ["Travel", "Study", "Fitness"]
            assert question["multi_select"] is False

            ws.send_json({"type": "question_response", "id": question["id"], "answer": "Travel"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    # No "tool_result" WS event either: the question_required/answered card
    # already fully represents this call, so session.py deliberately skips
    # sending the normal tool_result event for QUESTION_TOOL_NAMES (it would
    # otherwise show up as a redundant second "Asked: ..." row in the log --
    # see _stream_turn's ToolMessage branch). Confirm the answer really did
    # feed back into the model instead by inspecting the follow-up call.
    assert "tool_result" not in types
    tool_messages = [m for m in fake_model.received[-1] if isinstance(m, ToolMessage)]
    assert tool_messages[-1].name == "ask_user_question"
    assert "Travel" in json.dumps(tool_messages[-1].content)
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "done"


def test_ask_user_question_free_text_answer_works_too_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user can always type a custom answer instead of clicking a
    listed option -- there's nothing server-side that validates the
    answer against `options` at all, matching AskUserQuestion's own
    "Other" escape hatch."""
    call = _tool_call(
        "call_1",
        "ask_user_question",
        {"question": "What kind of plan?", "options": "Travel\nStudy"},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "help me plan something"})

            question = ws.receive_json()
            ws.send_json(
                {
                    "type": "question_response",
                    "id": question["id"],
                    "answer": "Actually, a birthday party",
                }
            )
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "tool_result" not in types
    tool_messages = [m for m in fake_model.received[-1] if isinstance(m, ToolMessage)]
    assert "birthday party" in json.dumps(tool_messages[-1].content)


def test_ask_user_question_is_not_blocked_by_plan_mode_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike a WRITE_LOCAL/EXEC/EXTERNAL tool, asking a question isn't a
    risky action plan mode's read-only guarantee needs to block -- it's
    pure communication, no side effect."""
    call = _tool_call(
        "call_1", "ask_user_question", {"question": "Which one?", "options": "A\nB"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/plan"})
            ws.receive_json()  # state (plan_mode: True)

            ws.send_json({"type": "user_message", "text": "ask me something"})
            question = ws.receive_json()
            assert question["type"] == "question_required"
            ws.send_json({"type": "question_response", "id": question["id"], "answer": "A"})
            _receive_until(ws, "tasks_changed")


def test_ask_user_question_hook_veto_becomes_a_respond_decision_not_a_reject_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A PreToolUse hook can still veto ask_user_question, but the
    HumanInTheLoopMiddleware interrupt for this tool only allows a
    "respond" decision -- a bare {"type": "reject", ...} would raise
    inside the middleware, so the veto reason has to be delivered as the
    tool's own "answer" instead."""
    hook_script = tmp_path / "veto_hook.py"
    hook_script.write_text("import sys; sys.stderr.write('no questions allowed'); sys.exit(1)\n")
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps({"PreToolUse": [f"{sys.executable} {hook_script}"]}), encoding="utf-8"
    )
    call = _tool_call(
        "call_1", "ask_user_question", {"question": "Which one?", "options": "A\nB"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="blocked")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t_q4") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "ask me something"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "question_required" not in types
    assert "tool_result" not in types
    tool_messages = [m for m in fake_model.received[-1] if isinstance(m, ToolMessage)]
    assert "no questions allowed" in json.dumps(tool_messages[-1].content)
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "blocked"


def test_stop_resolves_a_pending_question_with_a_placeholder_answer_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Proves request_stop() resolves a pending question rather than
    leaving the turn hanging forever -- the same "[stopped]" fallback text
    test_stop_denies_pending_approval_and_marks_reply_stopped's approval
    case gets (see _stream_turn's reply_text fallback), since the resumed
    astream's own cooperative stop check (checked after every chunk,
    including the ToolNode's ToolMessage carrying the placeholder answer)
    breaks the loop before any further model narration exists to report.
    The placeholder's actual content ("(Stopped by user before
    answering.)") isn't independently observable over the wire once
    resolved this way -- ask_user_question's tool_result WS event is
    deliberately suppressed (see _stream_turn's ToolMessage branch), same
    as the non-stopped answer path above -- so this only checks the
    contract a client actually sees: the turn ends promptly, not that it
    hangs waiting on a future nobody will ever resolve."""
    call = _tool_call(
        "call_1", "ask_user_question", {"question": "Which one?", "options": "A\nB"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="stopped")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q5") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "ask me something"})
            question = ws.receive_json()
            assert question["type"] == "question_required"

            ws.send_json({"type": "stop"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "tool_result" not in types
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "[stopped]"


def test_ask_user_question_multi_select_flag_and_option_parsing_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call(
        "call_1",
        "ask_user_question",
        {
            "question": "Which ones?",
            "options": "A\n\nB\nC\n",
            "multi_select": True,
        },
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_q6") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "ask me something"})
            question = ws.receive_json()
            assert question["multi_select"] is True
            # Blank lines in the newline-separated options string are
            # dropped, same as the non-empty-lines filter every other
            # newline/`---`-separated tool argument in this codebase uses.
            assert question["options"] == ["A", "B", "C"]

            ws.send_json(
                {"type": "question_response", "id": question["id"], "answer": "A, C"}
            )
            _receive_until(ws, "tasks_changed")


def test_pending_approval_is_redelivered_on_reconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The checkpointer-backed regression test for cli_lg.py's real
    # process-restart test (see runtime_lg/README.md) -- here, within one
    # process, connect once, trigger an approval, disconnect *without*
    # responding, then reconnect a fresh WebSocket to the same thread_id and
    # confirm the still-pending approval is redelivered rather than lost.
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t2b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            first_approval = ws.receive_json()
            assert first_approval["type"] == "approval_required"
        # disconnected without ever sending approval_response

        assert not (tmp_path / "workspace" / "note.txt").exists()

        with client.websocket_connect("/ws/t2b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            redelivered = ws.receive_json()
            assert redelivered["type"] == "approval_required"
            assert redelivered["tool_name"] == "write_file"

            ws.send_json(
                {"type": "approval_response", "id": redelivered["id"], "approved": True}
            )
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "done"
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"


def test_concurrent_sub_agents_ask_for_approval_in_the_panel_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two sub-agents delegated in one step run side by side; each one's
    approval is its own, answered by id, and neither leaks its messages
    into the parent's chat stream."""
    fake_model = ConcurrentSpawnFakeModel()
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_concurrent") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json(
                {
                    "type": "user_message",
                    "text": "Call spawn_agent TWICE in this turn, one for a.txt one for b.txt.",
                }
            )

            messages: list[dict[str, Any]] = []
            approvals: list[dict[str, Any]] = []
            while len(approvals) < 2:
                message = ws.receive_json()
                messages.append(message)
                if message["type"] == "approval_required":
                    approvals.append(message)
            for approval in approvals:
                approved = approval["arguments"]["path"] == "a.txt"
                ws.send_json(
                    {"type": "approval_response", "id": approval["id"], "approved": approved}
                )
            messages += _receive_until(ws, "tasks_changed")

    assert {a["arguments"]["path"] for a in approvals} == {"a.txt", "b.txt"}
    assert all(a["subagent_id"] for a in approvals)
    assert any(m["type"] == "subagents_changed" for m in messages)
    assert not any(m["type"] == "error" for m in messages)
    streamed = "".join(m["text"] for m in messages if m["type"] == "agent_delta")
    assert "sub-agent" not in streamed
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "parent done"
    assert (tmp_path / "workspace" / "a.txt").read_text() == "AAA"
    assert not (tmp_path / "workspace" / "b.txt").exists()
    tasks = SubAgentTaskStore(tmp_path / "state").list_for_thread("t_concurrent")
    assert sorted(t.status for t in tasks) == ["succeeded", "succeeded"]
    assert all(t.pending_approval is None and t.tool_uses == 1 for t in tasks)


def test_write_file_denied_is_not_executed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="ok, cancelled"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})

            approval = ws.receive_json()
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": False})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "ok, cancelled"
    assert not (tmp_path / "workspace" / "note.txt").exists()

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].decision == "reject"
    assert entries[0].reason == "human"


def test_clear_wipes_conversation_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_clear") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/clear"})
            cleared = ws.receive_json()
            assert cleared == {"type": "cleared"}

            # Behavioral proof the history is really gone, not just that the
            # event fired -- /compact's own "nothing much to compact" guard
            # (< 4 messages) only trips on a genuinely empty thread.
            ws.send_json({"type": "user_message", "text": "/compact"})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "Nothing much to compact" in error["message"]


def test_clear_queues_behind_a_pending_turn_instead_of_racing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression coverage for _turn_lock (see its docstring in __init__):
    a message sent while a turn is still pending on approval must *queue*
    behind it, not run concurrently -- so /clear here can't even reach its
    own "is anything pending?" check until the first turn's approval is
    answered and that turn fully finishes. Before _turn_lock existed, this
    exact scenario used to run /clear concurrently and see the pending
    approval immediately (asserted as an error); now it can't be observed
    at all through this path, since the lock always resolves the earlier
    turn first -- see _handle_clear's docstring for the one narrow window
    where that guard is still reachable."""
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_clear2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"

            # Queues behind _turn_lock -- nothing happens with this until
            # the pending approval below is answered.
            ws.send_json({"type": "user_message", "text": "/clear"})

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            first_turn_messages = _receive_until(ws, "tasks_changed")
            cleared = ws.receive_json()

    assert cleared == {"type": "cleared"}
    agent_message = next(m for m in first_turn_messages if m["type"] == "agent_message")
    assert agent_message["text"] == "done"


def test_stop_denies_pending_approval_and_marks_reply_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_stop") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"

            ws.send_json({"type": "stop"})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert "[stopped]" in agent_message["text"]
    assert not (tmp_path / "workspace" / "note.txt").exists()


def test_stop_hard_cancels_a_turn_stuck_inside_the_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real, user-reported bug: a provider SDK stuck in its own internal
    retry/backoff loop (e.g. langchain_google_genai on a Gemini quota
    error) never yields a chunk for the cooperative _stop_requested check
    to run against, so Stop did nothing for however long that loop ran
    (up to ~60-90s). request_stop() now hard-cancels the turn's own task
    whenever there's nothing pending to resolve instead (see its
    docstring) -- this proves the cancellation actually reaches a call
    genuinely stuck mid-stream, not just one paused between chunks."""
    started = threading.Event()
    fake_model = HangingChatModel(started=started)
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_hard_stop") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})

            # Wait until the turn has genuinely entered the hanging model
            # call (and _current_turn_task is therefore assigned) before
            # sending stop -- otherwise this races request_stop() against
            # handle_user_message's own task assignment.
            assert started.wait(timeout=5), "model call never started"

            ws.send_json({"type": "stop"})
            # HangingChatModel's _astream awaits asyncio.sleep(999) -- if
            # cancellation isn't actually reaching it, this receive blocks
            # for the test's own default timeout and fails loudly rather
            # than hanging for 999s.
            agent_message = ws.receive_json()

    assert agent_message["type"] == "agent_message"
    assert agent_message["text"] == "[stopped]"


def test_stop_mid_tool_closes_the_orphaned_tool_call_instead_of_breaking_the_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop hard-cancels a turn while a tool is still running: the
    checkpoint is left with an AIMessage whose tool_call never got a
    ToolMessage. The next message must reach the model with that call
    closed off (OpenAI-compatible providers 400 on the whole history
    otherwise), and reconnecting must not silently re-run the tool."""
    from langchain_core.messages import ToolCall

    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def slow_search_pdf(self: Any, query: str, **kwargs: Any) -> list[dict[str, object]]:
        calls.append(query)
        started.set()
        release.wait(timeout=10)
        return []

    monkeypatch.setattr("coscribe.tools.documents.DocumentToolkit.search_pdf", slow_search_pdf)
    call = ToolCall(name="search_pdf", args={"query": "Error code"}, id="call_1")
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="hi")]
    )
    try:
        with _client_lg(tmp_path, monkeypatch, fake_model) as client:
            with client.websocket_connect("/ws/t_stop_mid_tool") as ws:
                ws.receive_json()  # state
                ws.receive_json()  # history
                ws.send_json({"type": "user_message", "text": "search the manual"})
                assert started.wait(timeout=5), "tool never started"
                ws.send_json({"type": "stop"})
                stopped = _receive_until(ws, "agent_message")[-1]
                assert stopped["text"] == "[stopped]"
                release.set()

            with client.websocket_connect("/ws/t_stop_mid_tool") as ws:
                ws.receive_json()  # state
                ws.receive_json()  # history
                ws.send_json({"type": "user_message", "text": "just say hi"})
                messages = _receive_until(ws, "tasks_changed")
    finally:
        release.set()

    assert calls == ["Error code"]
    assert not any(m["type"] == "error" for m in messages)
    assert [m for m in messages if m["type"] == "agent_message"][-1]["text"] == "hi"
    history = fake_model.received[-1]
    tool_call_index = next(
        i for i, m in enumerate(history) if isinstance(m, AIMessage) and m.tool_calls
    )
    closing = history[tool_call_index + 1]
    assert isinstance(closing, ToolMessage)
    assert closing.tool_call_id == "call_1"
    assert isinstance(history[tool_call_index + 2], HumanMessage)


def test_close_orphaned_tool_calls_heals_a_thread_already_corrupted_mid_history() -> None:
    """A thread broken before the fix has a newer HumanMessage *after*
    the orphaned tool call -- the placeholder has to be inserted directly
    after its AIMessage, not appended at the end."""
    from langchain.agents import create_agent
    from langchain_core.messages import ToolCall
    from langgraph.checkpoint.memory import InMemorySaver

    from coscribe.web.session import _close_orphaned_tool_calls

    def lookup(q: str) -> str:
        """Look something up."""
        return "found"

    calls = [
        ToolCall(name="lookup", args={"q": "a"}, id="done"),
        ToolCall(name="lookup", args={"q": "b"}, id="orphan"),
    ]
    agent = create_agent(
        FakeToolCallingChatModel(responses=[]), [lookup], checkpointer=InMemorySaver()
    )
    config = {"configurable": {"thread_id": "t"}}

    async def scenario() -> list[BaseMessage]:
        await agent.aupdate_state(
            config,
            {
                "messages": [
                    HumanMessage(content="go"),
                    AIMessage(content="", tool_calls=calls),
                    ToolMessage(content="found", tool_call_id="done", name="lookup"),
                    HumanMessage(content="second"),
                ]
            },
            as_node="tools",
        )
        assert await _close_orphaned_tool_calls(agent, config) is True
        assert await _close_orphaned_tool_calls(agent, config) is False
        state = await agent.aget_state(config)
        assert state.next == ()
        return list(state.values["messages"])

    messages = asyncio.run(scenario())
    assert [type(m).__name__ for m in messages] == [
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
        "ToolMessage",
        "HumanMessage",
    ]
    assert [m.tool_call_id for m in messages[2:4]] == ["done", "orphan"]


def test_close_orphaned_tool_calls_commits_a_finished_parallel_calls_uncommitted_result() -> None:
    """Parallel tool calls, cancelled while one is still running: the one
    that finished only has an uncommitted pending write, which aget_state
    shows but a fresh input would drop. Its real result must survive, and
    only the unfinished call gets the placeholder."""
    from langchain.agents import create_agent
    from langchain_core.messages import ToolCall
    from langgraph.checkpoint.memory import InMemorySaver

    from coscribe.web.session import _ORPHANED_TOOL_CALL_NOTE, _close_orphaned_tool_calls

    started = threading.Event()
    release = threading.Event()

    def fast(q: str) -> str:
        """Fast lookup."""
        return "fast result"

    def slow(q: str) -> str:
        """Slow lookup."""
        started.set()
        release.wait(timeout=10)
        return "slow result"

    model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    ToolCall(name="fast", args={"q": "a"}, id="fast_call"),
                    ToolCall(name="slow", args={"q": "b"}, id="slow_call"),
                ],
            ),
            AIMessage(content="hi"),
        ]
    )
    agent = create_agent(model, [fast, slow], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t"}}

    async def scenario() -> None:
        turn = asyncio.create_task(
            agent.ainvoke({"messages": [HumanMessage(content="go")]}, config)
        )
        await asyncio.to_thread(started.wait, 5)
        await asyncio.sleep(0.2)
        turn.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await turn
        release.set()
        assert await _close_orphaned_tool_calls(agent, config) is True
        await agent.ainvoke({"messages": [HumanMessage(content="say hi")]}, config)

    try:
        asyncio.run(scenario())
    finally:
        release.set()

    history = model.received[-1]
    tool_messages = {m.tool_call_id: m.content for m in history if isinstance(m, ToolMessage)}
    assert tool_messages == {"fast_call": "fast result", "slow_call": _ORPHANED_TOOL_CALL_NOTE}
    assert isinstance(history[-1], HumanMessage)


def test_close_orphaned_tool_calls_commits_a_finished_tools_uncommitted_write() -> None:
    """Every tool call finished, but the superstep never committed: the
    ToolMessage exists only as a pending write, visible through aget_state
    yet dropped by a fresh input -- the exact state a live DeepSeek thread
    was found in. Nothing needs a placeholder; the write must still be
    committed or the model sees the orphan anyway."""
    from langchain.agents import create_agent
    from langchain_core.messages import ToolCall
    from langgraph.checkpoint.memory import InMemorySaver

    from coscribe.web.session import _close_orphaned_tool_calls

    def lookup(q: str) -> str:
        """Look something up."""
        return "unused"

    checkpointer = InMemorySaver()
    model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    agent = create_agent(model, [lookup], checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "t"}}
    call = ToolCall(name="lookup", args={"q": "a"}, id="call_1")

    async def scenario() -> None:
        await agent.aupdate_state(
            config,
            {"messages": [HumanMessage(content="go"), AIMessage(content="", tool_calls=[call])]},
            as_node="model",
        )
        state = await agent.aget_state(config)
        await checkpointer.aput_writes(
            state.config,
            [("messages", [ToolMessage(content="real result", tool_call_id="call_1")])],
            state.tasks[0].id,
        )
        assert await _close_orphaned_tool_calls(agent, config) is True
        await agent.ainvoke({"messages": [HumanMessage(content="say hi")]}, config)

    asyncio.run(scenario())

    history = model.received[-1]
    assert [type(m).__name__ for m in history] == [
        "HumanMessage",
        "AIMessage",
        "ToolMessage",
        "HumanMessage",
    ]
    assert history[2].content == "real result"


def test_switch_model_rebuilds_the_graph_and_reports_the_new_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model_a = FakeToolCallingChatModel(responses=[AIMessage(content="hi from A")])
    fake_model_b = FakeToolCallingChatModel(responses=[AIMessage(content="hi from B")])
    models = {"fake:model": fake_model_a, "fake:model-b": fake_model_b}
    with _client_lg(tmp_path, monkeypatch, fake_model_a) as client:
        # Must be set *after* entering _client_lg -- its own body sets a
        # default stub for resolve_chat_model on entry, which would
        # otherwise overwrite this one (same gotcha as
        # connect_one_mcp_server_lg elsewhere in this file).
        monkeypatch.setattr(
            "coscribe.web.session.resolve_chat_model",
            lambda model, custom_providers=None: models[model],
        )
        with client.websocket_connect("/ws/t_switch") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history
            assert state["model"] == "fake:model"

            ws.send_json({"type": "switch_model", "model": "fake:model-b"})
            switched_state = ws.receive_json()
            assert switched_state["type"] == "state"
            assert switched_state["model"] == "fake:model-b"

            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "hi from B"


def test_switch_model_rejects_a_string_without_a_provider_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_switch2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "switch_model", "model": "no-colon-here"})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "provider:model" in error["message"]


def test_switch_model_picks_up_a_provider_added_after_the_session_was_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real, live-reported bug: self._custom_providers used to only ever be
    loaded once, when a thread's ChatSessionLG was first created (see
    _get_session in web/app.py) -- adding a custom provider via the
    Providers tab while that thread was already open was invisible to it,
    so switch_model would raise resolve_chat_model's own "Unsupported
    provider" even though the provider genuinely was just configured.
    switch_model now reloads providers.json fresh on every switch instead
    of trusting that startup-time snapshot."""
    # chdir first -- add_provider's fallback path (no providers_config_path
    # configured yet) is a bare relative "./providers.json", resolved
    # against cwd (see test_post_provider_persists_an_absolute_path_not_a_
    # cwd_relative_one's own docstring for why this matters: skipping it
    # writes a real file into the repo root instead of tmp_path).
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    seen_custom_providers: list[dict[str, dict[str, str]] | None] = []

    def _fake_resolve(model: str, custom_providers: dict[str, dict[str, str]] | None = None) -> Any:
        seen_custom_providers.append(custom_providers)
        return fake_model

    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        # Must be set *after* entering _client_lg -- see the identical
        # gotcha noted on test_switch_model_rebuilds_the_graph above.
        monkeypatch.setattr("coscribe.web.session.resolve_chat_model", _fake_resolve)
        with client.websocket_connect("/ws/t_switch_new_provider") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history -- session object created here, no custom providers yet

            response = client.post(
                "/api/providers",
                json={
                    "name": "deepseek",
                    "base_url": "https://api.deepseek.com/v1",
                    "api_key": "sk-test",
                },
            )
            assert response.status_code == 200

            ws.send_json({"type": "switch_model", "model": "deepseek:deepseek-flash"})
            switched_state = ws.receive_json()
            assert switched_state["type"] == "state"
            assert switched_state["model"] == "deepseek:deepseek-flash"

    assert seen_custom_providers[-1] is not None
    assert seen_custom_providers[-1].get("deepseek") == {
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-test",
    }


def test_plan_mode_toggle_updates_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="unused")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/plan"})
            state = ws.receive_json()
            assert state == {
                "type": "state",
                "plan_mode": True,
                "accept_edits": False,
                "model": "fake:model",
                "context_window": 128_000,
                "enabled_skills": [
                    "Excel Spreadsheets",
                    "PPTX Slides",
                    "Skill Creator",
                    "Word Documents",
                ],
                "workspace_root": str(tmp_path / "workspace"),
                "workspace_explicit": False,
                "folders": [],
            }
            ws.send_json({"type": "user_message", "text": "/plan"})
            state = ws.receive_json()
            assert state["plan_mode"] is False


def test_accept_edits_toggle_updates_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="unused")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            state = ws.receive_json()
            assert state["accept_edits"] is True


def test_plan_mode_blocks_write_file_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="blocked"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4c") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/plan"})
            ws.receive_json()  # state (plan_mode: True)

            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "blocked"

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].decision == "reject"
    assert entries[0].reason == "plan_mode"
    assert not (tmp_path / "workspace" / "note.txt").exists()


def test_accept_edits_auto_approves_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4d") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            ws.receive_json()  # state (accept_edits: True)

            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "done"
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].decision == "approve"
    assert entries[0].reason == "accept_edits"


def test_compact_collapses_history_and_survives_a_later_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="hi there!"),
            AIMessage(content="nice to hear"),
            AIMessage(content="a short summary of the chat"),
            AIMessage(content="still here"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4e") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "how are you"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/compact"})
            compacted = ws.receive_json()
            assert compacted["type"] == "compacted"
            assert compacted["before"] == 4
            assert compacted["after"] == 1

            ws.send_json({"type": "user_message", "text": "are you still there?"})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "still here"


def test_compact_refuses_when_nothing_to_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t4f") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/compact"})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "Nothing much to compact" in error["message"]


def test_load_older_messages_reveals_pre_compact_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real, live-reported bug: after /compact, the chat log only ever
    showed the synthetic summary note -- no way to scroll up and see the
    real conversation, even though it was never actually deleted (see
    ChatSessionLG.load_older_messages' own docstring for the checkpoint
    mechanics). This is the fix's own regression test: the pre-compact
    turns come back verbatim on a "load_older_messages" request."""
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="hi there!"),
            AIMessage(content="nice to hear"),
            AIMessage(content="a short summary of the chat"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_older1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "how are you"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/compact"})
            compacted = ws.receive_json()
            assert compacted["type"] == "compacted"

            ws.send_json({"type": "load_older_messages"})
            older = ws.receive_json()
            # has_more is a "try again" hint, not a real lookahead (see
            # load_older_messages' own docstring) -- true here even
            # though this genuinely is the last epoch, confirmed instead
            # by a *second* call actually coming back empty below.
            ws.send_json({"type": "load_older_messages"})
            second_call = ws.receive_json()

    assert older["type"] == "older_messages"
    assert older["has_more"] is True
    texts = [(e["kind"], e.get("text")) for e in older["entries"]]
    assert texts == [
        ("user", "hello"),
        ("agent", "hi there!"),
        ("user", "how are you"),
        ("agent", "nice to hear"),
    ]
    assert second_call == {"type": "older_messages", "entries": [], "has_more": False}


def test_load_older_messages_is_empty_when_the_thread_was_never_compacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_older2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "load_older_messages"})
            older = ws.receive_json()

    assert older == {"type": "older_messages", "entries": [], "has_more": False}


def test_load_older_messages_across_two_compactions_reveals_each_epoch_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="reply one-a"),
            AIMessage(content="reply one-b"),
            AIMessage(content="first summary"),
            AIMessage(content="reply two-a"),
            AIMessage(content="reply two-b"),
            AIMessage(content="second summary"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_older3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            # /compact refuses below 4 messages (see _handle_compact) --
            # two turns per epoch here, same as
            # test_compact_collapses_history_and_survives_a_later_turn.
            ws.send_json({"type": "user_message", "text": "turn one-a"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "turn one-b"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "/compact"})
            assert ws.receive_json()["type"] == "compacted"

            ws.send_json({"type": "user_message", "text": "turn two-a"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "turn two-b"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "/compact"})
            assert ws.receive_json()["type"] == "compacted"

            ws.send_json({"type": "load_older_messages"})
            first_older = ws.receive_json()
            ws.send_json({"type": "load_older_messages"})
            second_older = ws.receive_json()
            ws.send_json({"type": "load_older_messages"})
            third_older = ws.receive_json()

    # The first compact's own synthetic note is a real HumanMessage
    # aupdate_state adds -- it becomes message #1 of the *next* epoch
    # going forward (the model's context after a compact genuinely
    # starts from that note), so it's what this epoch's own full
    # accumulated list starts with, same as the live conversation would
    # have shown it during epoch two itself.
    first_texts = [(e["kind"], e.get("text")) for e in first_older["entries"]]
    assert first_texts[0][0] == "user"
    assert "first summary" in first_texts[0][1]
    assert first_texts[1:] == [
        ("user", "turn two-a"),
        ("agent", "reply two-a"),
        ("user", "turn two-b"),
        ("agent", "reply two-b"),
    ]
    assert first_older["has_more"] is True
    assert [(e["kind"], e.get("text")) for e in second_older["entries"]] == [
        ("user", "turn one-a"),
        ("agent", "reply one-a"),
        ("user", "turn one-b"),
        ("agent", "reply one-b"),
    ]
    assert second_older["has_more"] is True
    # Nothing left before the very first turn -- and critically, this does
    # NOT re-return the first epoch's messages a second time (the real bug
    # a naive "always subset-check against the current checkpoint" version
    # of this would have hit -- see load_older_messages' own docstring).
    assert third_older == {"type": "older_messages", "entries": [], "has_more": False}


def test_edit_message_truncates_history_and_regenerates_from_the_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="hi there!"),
            AIMessage(content="nice to hear"),
            AIMessage(content="edited reply"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_edit1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "how are you"})
            _receive_until(ws, "tasks_changed")

            # index 1 -- the latest user turn -- edited. That turn onward
            # ("how are you", "nice to hear") is discarded, and the edited
            # text runs as a fresh turn; the earlier turn is untouched.
            ws.send_json({"type": "edit_message", "index": 1, "text": "how are you, edited"})
            messages = _receive_until(ws, "tasks_changed")

        # Reconnecting re-reads straight from the checkpointer -- proves
        # the truncation is real, not just a client-side illusion.
        with client.websocket_connect("/ws/t_edit1") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "edited reply"
    assert history["entries"] == [
        {"kind": "user", "text": "hello"},
        {"kind": "agent", "text": "hi there!"},
        {"kind": "user", "text": "how are you, edited"},
        {"kind": "agent", "text": "edited reply"},
    ]


def test_edit_message_rejects_anything_but_the_latest_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="hi there!"), AIMessage(content="nice to hear")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_edit_old") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "how are you"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "edit_message", "index": 0, "text": "hello, edited"})
            error = ws.receive_json()

        with client.websocket_connect("/ws/t_edit_old") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert error["type"] == "error"
    assert "most recent message" in error["message"]
    assert [e["text"] for e in history["entries"]] == [
        "hello",
        "hi there!",
        "how are you",
        "nice to hear",
    ]


def test_edit_message_rejects_an_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_edit2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "edit_message", "index": 5, "text": "no such turn"})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "No such message to edit" in error["message"]


def test_rewind_message_truncates_without_regenerating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: the UI's "Rewind"
    button used to call edit_message with the turn's own unedited text,
    which truncates *and* immediately reruns it -- that's retry, not
    rewind. rewind_message must truncate and stop there, with no new turn
    (and so no new agent_message) following it."""
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="hi there!"), AIMessage(content="nice to hear")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_rewind1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "how are you"})
            _receive_until(ws, "tasks_changed")

            # index 1 -- the second turn -- rewound. Only that turn's own
            # "how are you"/"nice to hear" is discarded; the first turn is
            # untouched, and nothing regenerates in its place.
            ws.send_json({"type": "rewind_message", "index": 1})
            rewound = ws.receive_json()

        with client.websocket_connect("/ws/t_rewind1") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert rewound == {"type": "rewound", "index": 1}
    assert history["entries"] == [
        {"kind": "user", "text": "hello"},
        {"kind": "agent", "text": "hi there!"},
    ]


def test_rewind_message_rejects_an_out_of_range_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_rewind2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "rewind_message", "index": 5})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "No such message to rewind" in error["message"]


def test_grpc_metadata_overflow_recovers_by_rebuilding_the_agent_and_retrying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: a long-lived Gemini
    session's gRPC channel accumulates something into its own metadata
    until a call fails with "429 ... Stream removed (received metadata
    size exceeds soft limit ...)" -- session.py's own
    _GRPC_METADATA_OVERFLOW_SIGNATURE comment has the full explanation.
    The turn must recover by rebuilding self.model/self.lg_agent and
    retrying once, not surface that error to the user on the first hit."""
    fake_model = GrpcMetadataOverflowThenSuccessModel(
        responses=[AIMessage(content="recovered")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_grpc_overflow") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            messages = _receive_until(ws, "tasks_changed")

    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "recovered"
    assert not any(m["type"] == "error" for m in messages)
    assert fake_model.calls == 2


def test_pretool_use_hook_denial_blocks_a_call_even_in_accept_edits_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook_script = tmp_path / "deny_hook.py"
    hook_script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        'sys.exit(1 if payload.get("tool_name") == "write_file" else 0)\n',
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps({"PreToolUse": [f"{sys.executable} {hook_script}"]}), encoding="utf-8"
    )
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="denied by hook"),
        ]
    )
    with _client_lg(
        tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path
    ) as client:
        with client.websocket_connect("/ws/t4g") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            ws.receive_json()  # state

            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "denied by hook"

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].decision == "reject"
    assert entries[0].reason == "hook_veto"
    assert entries[0].detail is not None
    assert not (tmp_path / "workspace" / "note.txt").exists()


def test_pretool_use_hook_gates_a_low_risk_tool_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When PreToolUse hooks are configured, even a tool that never
    required approval (task_create, risk_category="READ") is routed through the
    same interrupt point so the hook can veto it -- see agent.py's
    extra_interrupt_tool_names. It's still auto-approved (no
    approval_required sent) since the hook allows it and it was never
    risky enough to ask the user either way."""
    hook_script = tmp_path / "log_hook.py"
    log_path = tmp_path / "pretool.log"
    hook_script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        f'open({str(log_path)!r}, "a").write(payload["tool_name"] + chr(10))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps({"PreToolUse": [f"{sys.executable} {hook_script}"]}), encoding="utf-8"
    )
    call = _tool_call("call_1", "task_create", {"content": "write the report"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="tracked it"),
        ]
    )
    with _client_lg(
        tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path
    ) as client:
        with client.websocket_connect("/ws/t4h") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "please plan this out"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "tracked it"
    assert log_path.read_text(encoding="utf-8").strip() == "task_create"


def test_post_tool_use_hook_receives_the_tool_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hook_script = tmp_path / "posttool_hook.py"
    log_path = tmp_path / "posttool.log"
    hook_script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        f'open({str(log_path)!r}, "a").write(json.dumps(payload) + chr(10))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps({"PostToolUse": [f"{sys.executable} {hook_script}"]}), encoding="utf-8"
    )
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(
        tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path
    ) as client:
        with client.websocket_connect("/ws/t4i") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            ws.send_json(
                {"type": "approval_response", "id": approval["id"], "approved": True}
            )
            _receive_until(ws, "tasks_changed")

    logged = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert logged["tool_name"] == "write_file"
    assert logged["arguments"] == {"path": "note.txt", "content": "hi"}
    assert logged["result"]["bytes_written"] == 2


def _wait_for_file(path: Path, timeout: float = 10.0) -> None:
    """Some hook events (Interrupt, SessionEnd) fire from a background
    task/exception handler rather than inline with a message the test can
    wait on with ws.receive_json() -- unlike a normal request/response
    exchange, there's no built-in synchronization point proving the
    server has actually finished running the hook by the time the test's
    own code continues, so this polls briefly instead of asserting
    immediately."""
    deadline = time.monotonic() + timeout
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)


def _logging_hook_config(tmp_path: Path, event: str, log_path: Path) -> Path:
    """Same pattern as test_post_tool_use_hook_receives_the_tool_result
    above: a hook script that appends its received JSON payload to
    log_path, one line per invocation -- shared by the Phase 7 item 3
    event tests below (SessionEnd/UserPromptSubmit/PreCompact/
    PostCompact/Interrupt) since they all just need to prove the payload
    actually arrived, not exercise anything event-specific about how the
    hook script itself behaves."""
    hook_script = tmp_path / f"{event.lower()}_hook.py"
    hook_script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        f'open({str(log_path)!r}, "a").write(json.dumps(payload) + chr(10))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps({event: [f"{sys.executable} {hook_script}"]}), encoding="utf-8"
    )
    return hooks_path


def test_user_prompt_submit_hook_receives_the_message_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "log.jsonl"
    hooks_path = _logging_hook_config(tmp_path, "UserPromptSubmit", log_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t_hook_ups") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello there"})
            _receive_until(ws, "tasks_changed")

    logged = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert logged["event"] == "UserPromptSubmit"
    assert logged["text"] == "hello there"


def test_pre_and_post_compact_hooks_fire_around_manual_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pre_log = tmp_path / "pre.jsonl"
    post_log = tmp_path / "post.jsonl"
    pre_script = tmp_path / "pre_hook.py"
    pre_script.write_text(
        "import json, sys\n"
        "json.load(sys.stdin)\n"
        f'open({str(pre_log)!r}, "a").write("fired\\n")\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    post_script = tmp_path / "post_hook.py"
    post_script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        f'open({str(post_log)!r}, "a").write(json.dumps(payload) + chr(10))\n'
        "sys.exit(0)\n",
        encoding="utf-8",
    )
    hooks_path = tmp_path / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "PreCompact": [f"{sys.executable} {pre_script}"],
                "PostCompact": [f"{sys.executable} {post_script}"],
            }
        ),
        encoding="utf-8",
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content=f"reply {i}") for i in range(6)]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t_hook_compact") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            for i in range(3):
                ws.send_json({"type": "user_message", "text": f"message {i}"})
                _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "/compact"})
            compacted = ws.receive_json()
            assert compacted["type"] == "compacted"

    assert pre_log.read_text(encoding="utf-8").strip() == "fired"
    logged = json.loads(post_log.read_text(encoding="utf-8").strip())
    assert logged["event"] == "PostCompact"
    assert logged["before"] == compacted["before"]
    assert logged["after"] == compacted["after"]


def test_interrupt_hook_fires_on_a_stop_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "log.jsonl"
    hooks_path = _logging_hook_config(tmp_path, "Interrupt", log_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t_hook_interrupt") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "stop"})
            _wait_for_file(log_path)

    logged = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert logged["event"] == "Interrupt"
    assert logged["thread_id"] == "t_hook_interrupt"


def test_session_end_hook_fires_on_disconnect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_path = tmp_path / "log.jsonl"
    hooks_path = _logging_hook_config(tmp_path, "SessionEnd", log_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model, hooks_config_path=hooks_path) as client:
        with client.websocket_connect("/ws/t_hook_end") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            # Close explicitly and wait *inside* this `with` block, same
            # pattern as test_interrupt_hook_fires_on_a_stop_message right
            # above -- not incidental. Found live, genuinely flaky (not a
            # CI-only artifact -- reproduced locally too): starlette's own
            # WebSocketTestSession.__exit__ (relied on if this block is
            # left to close the socket implicitly on exit) sends the
            # disconnect message and then *immediately* hard-cancels the
            # whole in-flight ASGI app task via its cancel scope, racing
            # ahead of app.py's own `except WebSocketDisconnect:` handler
            # actually finishing `await session.run_session_end_hooks()`
            # (which shells out to a real subprocess) -- confirmed by
            # reading starlette/testclient.py's own __exit__/_run methods,
            # not guessed. Closing here instead means the wait below has
            # real, uncancelled wall-clock time for the hook to actually
            # run before this `with` block's own __exit__ can race it.
            ws.close()
            _wait_for_file(log_path)

    logged = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert logged["event"] == "SessionEnd"
    assert logged["thread_id"] == "t_hook_end"


def test_task_list_endpoint_reflects_task_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "task_create", {"content": "write the report"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="tracked it"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t5") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "please plan this out"})
            _receive_until(ws, "tasks_changed")

        response = client.get("/api/threads/t5/tasks")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["content"] == "write the report"
    assert tasks[0]["status"] == "pending"


def test_get_tools_lists_builtin_tools_with_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/tools")

    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.json()["tools"]}
    assert tools["read_docx"]["category"] == "documents"
    assert tools["write_xlsx"]["requires_approval"] is True
    assert tools["task_create"]["category"] == "tasks"
    assert tools["web_search"]["category"] == "web"
    assert tools["web_search"]["requires_approval"] is False
    assert tools["add_xlsx_chart"]["category"] == "documents"
    assert tools["add_xlsx_chart"]["requires_approval"] is True
    assert tools["recalc_xlsx"]["category"] == "documents"
    assert tools["recalc_xlsx"]["requires_approval"] is True
    assert tools["format_xlsx_cells"]["category"] == "documents"
    assert tools["format_xlsx_cells"]["requires_approval"] is True
    assert tools["add_pptx_chart"]["category"] == "documents"
    assert tools["add_pptx_chart"]["requires_approval"] is True
    assert tools["add_pptx_image"]["category"] == "documents"
    assert tools["add_pptx_image"]["requires_approval"] is True
    assert tools["set_pptx_notes"]["category"] == "documents"
    assert tools["set_pptx_notes"]["requires_approval"] is True
    assert tools["set_pptx_transition"]["category"] == "documents"
    assert tools["set_pptx_transition"]["requires_approval"] is True
    assert tools["add_pptx_animation"]["category"] == "documents"
    assert tools["add_pptx_animation"]["requires_approval"] is True
    assert tools["add_pptx_hyperlink"]["category"] == "documents"
    assert tools["add_pptx_hyperlink"]["requires_approval"] is True
    assert tools["edit_pptx_theme_colors"]["category"] == "documents"
    assert tools["edit_pptx_theme_colors"]["requires_approval"] is True
    assert tools["run_python_script"]["category"] == "scripts"
    assert tools["run_python_script"]["risk_category"] == "EXEC"
    assert tools["run_python_script"]["requires_approval"] is True
    assert tools["run_node_script"]["category"] == "scripts"
    assert tools["run_node_script"]["risk_category"] == "EXEC"
    assert tools["run_node_script"]["requires_approval"] is True
    assert tools["set_pptx_background_image"]["category"] == "documents"
    assert tools["set_pptx_background_image"]["requires_approval"] is True
    assert tools["edit_pptx_text"]["category"] == "documents"
    assert tools["edit_pptx_text"]["requires_approval"] is True
    assert tools["delete_pptx_slide"]["category"] == "documents"
    assert tools["delete_pptx_slide"]["requires_approval"] is True
    assert tools["duplicate_pptx_slide"]["category"] == "documents"
    assert tools["duplicate_pptx_slide"]["requires_approval"] is True
    assert tools["reorder_pptx_slide"]["category"] == "documents"
    assert tools["reorder_pptx_slide"]["requires_approval"] is True
    assert tools["edit_file"]["category"] == "filesystem"
    assert tools["edit_file"]["requires_approval"] is True
    assert tools["edit_file_batch"]["category"] == "filesystem"
    assert tools["edit_file_batch"]["requires_approval"] is True
    assert tools["get_file_info"]["category"] == "filesystem"
    assert tools["get_file_info"]["requires_approval"] is False
    assert tools["delete_file"]["category"] == "filesystem"
    assert tools["delete_file"]["requires_approval"] is True
    assert tools["move_file"]["category"] == "filesystem"
    assert tools["move_file"]["requires_approval"] is True
    assert tools["copy_file"]["category"] == "filesystem"
    assert tools["copy_file"]["requires_approval"] is True
    assert tools["list_pptx_shapes"]["category"] == "documents"
    assert tools["list_pptx_shapes"]["requires_approval"] is False
    assert tools["edit_pptx_shape"]["category"] == "documents"
    assert tools["edit_pptx_shape"]["requires_approval"] is True
    assert tools["delete_pptx_shape"]["category"] == "documents"
    assert tools["delete_pptx_shape"]["requires_approval"] is True
    assert tools["replace_pptx_image"]["category"] == "documents"
    assert tools["replace_pptx_image"]["requires_approval"] is True
    assert tools["list_pptx_icons"]["category"] == "documents"
    assert tools["list_pptx_icons"]["requires_approval"] is False
    assert tools["add_pptx_icon"]["category"] == "documents"
    assert tools["add_pptx_icon"]["requires_approval"] is True
    assert tools["recolor_pptx_icon"]["category"] == "documents"
    assert tools["recolor_pptx_icon"]["requires_approval"] is True
    assert tools["edit_pptx_table_cell"]["category"] == "documents"
    assert tools["edit_pptx_table_cell"]["requires_approval"] is True
    assert tools["merge_pptx_table_cells"]["category"] == "documents"
    assert tools["merge_pptx_table_cells"]["requires_approval"] is True
    assert tools["search_images"]["category"] == "web"
    assert tools["search_images"]["requires_approval"] is False
    assert tools["download_image"]["category"] == "web"
    assert tools["download_image"]["requires_approval"] is True
    assert tools["add_pptx_scrim"]["category"] == "documents"
    assert tools["add_pptx_scrim"]["requires_approval"] is True
    assert tools["sleep_until"]["category"] == "selfwake"
    assert tools["sleep_until"]["requires_approval"] is True
    assert tools["list_wakes"]["category"] == "selfwake"
    assert tools["list_wakes"]["requires_approval"] is False


def test_wake_poll_loop_starts_and_cancels_cleanly_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural check for web/app.py's lifespan-owned background poll
    loop (see runtime_lg/selfwake.py's poll_due_wakes -- functional
    coverage of poll_due_wakes itself lives in test_selfwake_resume.py,
    driven directly without the FastAPI/WebSocket layer around it). Stubs
    poll_due_wakes to just count calls, and sets wake_poll_seconds to 0 so
    the loop iterates as fast as the event loop lets it during the brief
    window the client is open -- confirms the loop actually runs (not just
    that it doesn't crash), and that TestClient.__exit__ (which runs the
    lifespan's shutdown half, cancelling the loop task) completes without
    raising -- a leaked/never-cancelled task or an unhandled
    CancelledError would surface as a test failure here."""
    calls = 0

    async def _counting_poll_due_wakes(state_dir: Any, get_session: Any) -> list[Any]:
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr("coscribe.web.app.poll_due_wakes", _counting_poll_due_wakes)
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model, wake_poll_seconds=0) as client:
        time.sleep(0.05)
        client.get("/api/tools")  # any request -- just keeps the client's event loop alive

    assert calls > 0


def test_background_event_bus_fans_out_to_every_subscriber() -> None:
    """Unit-level coverage of background_events.BackgroundEventBus itself,
    independent of FastAPI/asyncio-loop plumbing: two subscribers each get
    their own copy of a published event, and an unsubscribed queue gets
    nothing further."""
    import asyncio

    from coscribe.web.background_events import BackgroundEvent, BackgroundEventBus

    async def _run() -> None:
        bus = BackgroundEventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.publish(
            BackgroundEvent(kind="wake", status="completed", title="research done", thread_id="t1")
        )
        e1 = q1.get_nowait()
        e2 = q2.get_nowait()
        assert e1 == e2 == {
            "type": "background_run_completed",
            "kind": "wake",
            "status": "completed",
            "title": "research done",
            "thread_id": "t1",
        }
        bus.unsubscribe(q1)
        bus.publish(
            BackgroundEvent(kind="wake", status="completed", title="ignored", thread_id="t1")
        )
        assert q1.empty()
        assert q2.get_nowait()["title"] == "ignored"

    asyncio.run(_run())


def test_wake_poll_loop_publishes_background_events_for_fired_wakes_and_triggers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The desktop shell's tray notification (office-agent-desktop) needs
    a channel outside any browser WebSocket to learn a background/
    scheduled run finished -- background_events.py's BackgroundEventBus,
    fed from _wake_poll_loop's own return values from poll_due_wakes/
    poll_due_scheduled_tasks (see that loop's own comments), and exposed
    as app.state.background_events for exactly this kind of test. Reaches
    it via client.portal (the same anyio BlockingPortal TestClient itself
    uses to run the app's event loop) rather than the real /internal/
    events SSE endpoint: that endpoint's response never completes by
    design (a persistent stream), and starlette's synchronous TestClient
    only ever returns a response once the ASGI app's call finishes --
    confirmed live, driving it through actual HTTP here just hangs
    forever. The endpoint itself is covered separately, structurally,
    below."""
    import types

    async def _fake_poll_due_wakes(state_dir: Any, get_session: Any) -> list[Any]:
        return [types.SimpleNamespace(reason="research done", thread_id="thread-a")]

    async def _fake_poll_due_scheduled_tasks(
        state_dir: Any, get_session: Any, on_pruned: Any = None
    ) -> list[Any]:
        run = types.SimpleNamespace(thread_id="thread-b", status="failed")
        return [types.SimpleNamespace(name="daily digest", runs=[run])]

    monkeypatch.setattr("coscribe.web.app.poll_due_wakes", _fake_poll_due_wakes)
    monkeypatch.setattr(
        "coscribe.web.app.poll_due_scheduled_tasks", _fake_poll_due_scheduled_tasks
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model, wake_poll_seconds=0) as client:
        bus = client.app.state.background_events
        queue = client.portal.call(bus.subscribe)
        first = client.portal.call(queue.get)
        second = client.portal.call(queue.get)

    events = {(e["kind"], e["status"], e["title"], e["thread_id"]) for e in (first, second)}
    assert events == {
        ("wake", "completed", "research done", "thread-a"),
        ("scheduled_task", "failed", "daily digest", "thread-b"),
    }


def test_internal_events_endpoint_is_a_registered_sse_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural check that /internal/events exists, is GET-only, and
    responds with a text/event-stream StreamingResponse -- the part of
    background_events_stream (web/app.py) that a real HTTP request never
    finishes exercising in this test suite (see the previous test's
    docstring), covered here without actually driving the infinite
    stream: call the route's endpoint function directly and inspect the
    response object, then explicitly close its body iterator so the
    subscription it opened doesn't linger."""
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        route = next(r for r in client.app.routes if getattr(r, "path", None) == "/internal/events")
        assert route.methods == {"GET"}

        async def _probe() -> None:
            response = await route.endpoint()
            assert response.media_type == "text/event-stream"
            await response.body_iterator.aclose()

        client.portal.call(_probe)


def test_reviewer_tools_are_exactly_the_read_only_documents_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session.py's _build_lg_tools hands review_work's reviewer exactly
    the tools with category=="documents" and requires_approval==False --
    read_docx/read_pdf/search_pdf/read_xlsx/read_pptx/render_pptx_preview/
    list_pptx_shapes/list_pptx_shape_types/list_pptx_transition_types/
    list_pptx_animation_types/list_pptx_icons/read_pptx_theme_colors/
    read_pptx_xml/check_pptx_delivery today. This is the contract that
    filter depends on: if a future "documents" tool is added without
    requires_approval=True, it would silently become reviewer-callable
    too (fine if read-only, a real bug if not) -- and if one of these
    ever moves out of "documents" or gains requires_approval, the
    reviewer silently loses independent-verification power. Testing
    against the real, non-mocked coordinator tool list, not a hand-built
    stand-in list."""
    from coscribe.coordinator import build_coordinator_agent
    from coscribe.runtime.types import get_tool_metadata

    settings = Settings(
        _env_file=None,
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        default_model="gemini:gemini-flash-latest",
    )
    agent = build_coordinator_agent(settings, "test-thread")
    reviewer_tools = [
        t
        for t in agent.tools
        if get_tool_metadata(t).category == "documents"
        and not get_tool_metadata(t).requires_approval
    ]
    names = sorted(t.__name__ if hasattr(t, "__name__") else t.name for t in reviewer_tools)
    assert names == [
        "check_pptx_delivery",
        "list_pptx_animation_types",
        "list_pptx_icons",
        "list_pptx_shape_types",
        "list_pptx_shapes",
        "list_pptx_transition_types",
        "read_docx",
        "read_pdf",
        "read_pptx",
        "read_pptx_theme_colors",
        "read_pptx_xml",
        "read_xlsx",
        "render_pptx_preview",
        "search_pdf",
    ]


def test_upload_writes_file_to_workspace_and_returns_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/upload",
            files={"file": ("report.pdf", b"%PDF-1.4 fake content", "application/pdf")},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "report.pdf"
    assert body["bytes_written"] == len(b"%PDF-1.4 fake content")
    assert (tmp_path / "workspace" / "report.pdf").read_bytes() == b"%PDF-1.4 fake content"


def test_upload_auto_renames_on_name_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        first = client.post("/api/upload", files={"file": ("notes.txt", b"first", "text/plain")})
        second = client.post(
            "/api/upload", files={"file": ("notes.txt", b"second", "text/plain")}
        )

    assert first.json()["path"] == "notes.txt"
    assert second.json()["path"] == "notes (1).txt"
    workspace = tmp_path / "workspace"
    assert (workspace / "notes.txt").read_bytes() == b"first"
    assert (workspace / "notes (1).txt").read_bytes() == b"second"


def test_upload_sanitizes_path_traversal_attempt_in_filename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/upload", files={"file": ("../../evil.txt", b"data", "text/plain")}
        )

    assert response.status_code == 200
    assert response.json()["path"] == "evil.txt"
    assert (tmp_path / "workspace" / "evil.txt").read_bytes() == b"data"
    assert not (tmp_path / "evil.txt").exists()


def test_upload_rejects_oversized_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coscribe.web import app as app_module

    monkeypatch.setattr(app_module, "MAX_UPLOAD_BYTES", 10)
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/upload", files={"file": ("big.txt", b"0123456789 -- too big", "text/plain")}
        )

    assert response.status_code == 413
    assert not (tmp_path / "workspace" / "big.txt").exists()


def test_get_preview_serves_an_existing_preview_png(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previews_dir = tmp_path / "state" / "previews"
    previews_dir.mkdir(parents=True)
    name = "0123456789abcdef0123456789abcdef.png"
    (previews_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n fake png bytes")

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get(f"/api/previews/{name}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == b"\x89PNG\r\n\x1a\n fake png bytes"


def test_get_preview_404s_for_unknown_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/previews/0123456789abcdef0123456789abcdef.png")

    assert response.status_code == 404


def test_get_preview_rejects_path_traversal_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Proves the name-shape check, not just a missing-file 404 -- a name
    # that doesn't match render_thumbnail's uuid4().hex pattern is rejected
    # before any filesystem lookup, so it can never escape state_dir/previews/.
    secret = tmp_path / "state" / "secret.txt"
    secret.parent.mkdir(parents=True)
    secret.write_text("do not serve me")

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/previews/..%2Fsecret.txt")

    assert response.status_code == 404


def test_get_pptx_shapes_returns_shapes_and_slide_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backs the click-a-shape-in-the-preview feature (ChatLog.tsx's
    PptxShapeOverlay) -- a plain UI-facing REST read, not a tool call."""
    _write_test_deck(tmp_path / "workspace")

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/pptx-shapes", params={"path": "deck.pptx", "slide": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["slide"] == 1
    assert body["shape_count"] == 1
    assert body["slide_width_in"] > 0
    assert body["slide_height_in"] > 0
    [shape] = body["shapes"]
    assert shape["index"] == 0
    assert shape["left_in"] == pytest.approx(1.0)
    assert shape["top_in"] == pytest.approx(1.0)
    assert shape["width_in"] == pytest.approx(2.0)
    assert shape["height_in"] == pytest.approx(1.0)
    assert shape["text_preview"] == "hello"


def test_get_pptx_shapes_400s_for_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/pptx-shapes", params={"path": "nope.pptx", "slide": 1})

    assert response.status_code == 400


def test_get_pptx_shapes_400s_for_an_out_of_range_slide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_test_deck(tmp_path / "workspace")

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/pptx-shapes", params={"path": "deck.pptx", "slide": 5})

    assert response.status_code == 400


def test_get_pptx_shapes_400s_for_a_path_outside_the_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = tmp_path / "outside.pptx"
    Presentation().save(str(outside))

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/pptx-shapes", params={"path": str(outside), "slide": 1})

    assert response.status_code == 400


def test_get_commands_includes_plan_accept_edits_and_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/commands")

    assert response.status_code == 200
    names = {command["name"] for command in response.json()}
    assert {"init", "plan", "accept-edits", "compact"} <= names


def test_get_commands_lists_skills_by_slug_not_display_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/commands' "name" field is what the frontend inserts verbatim
    after "/" (Composer.tsx's selectAutocomplete) -- for a skill it has to
    be SkillInfo.slug (a single token, e.g. "skill-creator"), never
    skill.name (a display label that can contain spaces, e.g. "Skill
    Creator") -- a space there could never be typed as one command word
    to begin with. See the WS-level test below for the matching half."""
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/commands")

    commands = {command["name"]: command["description"] for command in response.json()}
    assert "skill-creator" in commands
    assert "word" in commands
    assert "Skill Creator" not in commands
    assert "Word Documents" not in commands


def test_slash_skill_slug_force_loads_a_multiword_named_skill_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, previously-shipped-but-untested bug:
    web/session.py's own skills_by_slug (then named skills_by_name) used
    to be keyed by skill.name.lower() -- for "Skill Creator" that's
    "skill creator", with a space -- but _handle_user_message_locked only
    ever looks up the single word before the first space in the typed
    text ("/word", rest="creator ..."), so no multi-word-named skill
    (every current built-in) could ever actually be force-loaded this
    way. Now keyed by SkillInfo.slug, a single command-safe token."""
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="ok")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_slash_skill") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/skill-creator name a new skill"})
            _receive_until(ws, "tasks_changed")

    assert len(fake_model.received) == 1
    human_messages = [m for m in fake_model.received[0] if isinstance(m, HumanMessage)]
    assert human_messages, "expected a human message in the request"
    text = human_messages[-1].content
    assert isinstance(text, str)
    assert "Skill 'Skill Creator' invoked directly via /skill-creator" in text
    assert "Mirrors Claude Code's own bundled skill-creator" in text
    assert "User request: name a new skill" in text


def test_unknown_slash_command_is_still_rejected_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_slash_unknown") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/nope do something"})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "Unknown command: /nope" in error["message"]
    assert fake_model.i == 0


# -- /api/config, /api/mcp/*, /api/providers/* -- see app.py's module
# docstring for the one behavioral difference from web/app.py's identical
# endpoints: a change here applies to the *next new* session, not every
# already-open one (runtime_lg's compiled graph can't be hot-mutated the
# way the old runtime's per-turn tool/model resolution can).


def test_get_config_masks_provider_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=sk-1234567890abcdef\n", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/config")

    assert response.status_code == 200
    body = response.json()
    assert body["GEMINI_API_KEY"]["set"] is True
    assert body["GEMINI_API_KEY"]["masked"] != "sk-1234567890abcdef"
    assert body["GEMINI_API_KEY"]["masked"].endswith("cdef")


def test_post_config_updates_env_and_rejects_bad_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This endpoint sets os.environ["GEMINI_API_KEY"] directly (mirroring
    # web/app.py's identical update_config) -- monkeypatch can't auto-clean
    # that up for later tests since it wasn't the one that set it. Not
    # order-*dependent* the way the MCP-config-path test is (this always
    # runs unconditionally, no "only if unset" branch to skip), but
    # asserting the exact value written is only meaningful starting from a
    # known-clean slate.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/config",
            json={
                "updates": {
                    "GEMINI_API_KEY": "sk-newkey",
                    "COSCRIBE_DEFAULT_MODEL": "no-colon-here",
                }
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["rejected"] == {
        "COSCRIBE_DEFAULT_MODEL": (
            'must be a "provider:model" string, e.g. "anthropic:sonnet"'
        )
    }
    assert dotenv_values(tmp_path / ".env")["GEMINI_API_KEY"] == "sk-newkey"
    assert os.environ["GEMINI_API_KEY"] == "sk-newkey"


def test_get_config_includes_background_on_close_desktop_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """COSCRIBE_BACKGROUND_ON_CLOSE is Rust-consumed (office-agent-desktop's
    lib.rs reads the same .env file directly, see DESKTOP_ENV_VARS's own
    comment) -- this Python process never branches on it, but /api/config
    still has to surface it for the Settings panel's toggle to read/write,
    same as every other desktop-facing value that lives in this file."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("COSCRIBE_BACKGROUND_ON_CLOSE=false\n", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/config")

    assert response.status_code == 200
    assert response.json()["COSCRIBE_BACKGROUND_ON_CLOSE"] == "false"


def test_post_config_can_set_background_on_close_without_requiring_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike every COSCRIBE_ENV_VARS entry, this one must NOT mark
    restart_required -- the Rust shell re-reads .env live at the next
    window close, no coscribe-web restart needed (see DESKTOP_ENV_VARS's
    own comment for why it's a separate list from COSCRIBE_ENV_VARS)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/config", json={"updates": {"COSCRIBE_BACKGROUND_ON_CLOSE": "false"}}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["rejected"] == {}
    assert body["restart_required"] is False
    assert dotenv_values(tmp_path / ".env")["COSCRIBE_BACKGROUND_ON_CLOSE"] == "false"


def test_get_and_post_memory_round_trip_the_global_instructions_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET/POST /api/memory back the Settings panel's Global Instructions
    editor (GeneralTab.tsx's GlobalInstructionsSection) -- a direct
    read/overwrite of MEMORY.md's content, distinct from the `remember`
    tool (which only ever appends one bullet at a time)."""
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        empty = client.get("/api/memory")
        assert empty.status_code == 200
        assert empty.json() == {"content": ""}

        posted = client.post("/api/memory", json={"content": "- prefers concise replies"})
        assert posted.status_code == 200
        assert posted.json() == {"status": "ok"}

        refreshed = client.get("/api/memory")
        assert refreshed.json() == {"content": "- prefers concise replies"}

    assert (tmp_path / "MEMORY.md").read_text(encoding="utf-8") == "- prefers concise replies"


def test_get_mcp_catalog_returns_curated_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/mcp/catalog")

    assert response.status_code == 200
    entries = response.json()
    names = {entry["name"] for entry in entries}
    assert {"playwright", "office365"} <= names
    # What coscribe already covers itself (reading pages, memory, the
    # date) isn't offered as a connector.
    assert not names & {"fetch", "memory", "sequential-thinking", "time", "slack"}

    office365 = next(entry for entry in entries if entry["name"] == "office365")
    # One-click -- its own MCP server handles the device-code sign-in
    # through its own login/verify-login tools, no coscribe-side config
    # to prefill.
    assert "needs_config" not in office365
    assert "env" not in office365


def test_post_mcp_server_persists_config_and_sets_env_var_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same real-os.environ-pollution risk as test_web.py's identical test --
    # see that test's comment for the full explanation. create_app_lg's own
    # load_dotenv(".env") call is the culprit here too.
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/mcp/servers",
            json={"name": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
        )

        assert response.status_code == 200
        # connect_one_mcp_server_lg is stubbed to [] by _client_lg -- "saved
        # but didn't connect live" -- see the dedicated splice-in test below
        # for the case where it actually returns tools.
        assert response.json() == {"rejected": {}, "connected": False, "error": None}

        mcp_config = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
        assert mcp_config["mcpServers"]["fetch"]["command"] == "uvx"
        assert dotenv_values(tmp_path / ".env")["COSCRIBE_MCP_CONFIG_PATH"] == str(
            tmp_path / "mcp.json"
        )

        servers = client.get("/api/mcp/servers").json()
        assert servers == {
            "fetch": {
                "command": "uvx",
                "args": ["mcp-server-fetch"],
                "masked_env": {},
                "connected": False,
            }
        }


def test_post_mcp_server_reconnect_retries_a_previously_failed_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real gap: once a server is added (`isAdded`
    true), the Connectors panel had no way to replay a failed connect --
    only Remove + re-add, which also throws away the saved config.
    /reconnect re-runs connect_one_mcp_server_lg against the *same*
    persisted config, so a connector that failed once (e.g. a timeout,
    a stale npm cache) can be retried without deleting it first."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        # connect_one_mcp_server_lg is stubbed to [] by _client_lg -- saved
        # but not connected, same as the first attempt failing.
        add_response = client.post(
            "/api/mcp/servers",
            json={"name": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
        )
        assert add_response.json() == {"rejected": {}, "connected": False, "error": None}

        # Simulates the underlying problem being resolved before the retry
        # (e.g. the user installed uv) -- this time connect succeeds.
        class _FakeConnection:
            async def close(self) -> None:
                pass

        async def _fake_connect_succeeds(name: str, config: Any) -> tuple[list[Any], Any, None]:
            from coscribe.runtime.types import tool_metadata

            def _tool(x: str = "") -> str:
                """fake"""
                return x

            _tool.__name__ = "fetch__tool"
            tool_metadata(_tool, risk_category="READ", category="mcp:fetch")
            return [_tool], _FakeConnection(), None

        monkeypatch.setattr(
            "coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", _fake_connect_succeeds
        )

        reconnect_response = client.post("/api/mcp/servers/fetch/reconnect")
        assert reconnect_response.json() == {"connected": True, "error": None}

        servers = client.get("/api/mcp/servers").json()
        assert servers["fetch"]["connected"] is True
        # The persisted config itself is untouched by a reconnect -- only
        # bump-version is allowed to rewrite mcp.json.
        mcp_config = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
        assert mcp_config["mcpServers"]["fetch"]["command"] == "uvx"


def test_post_mcp_server_reconnect_reports_not_found_for_an_unconfigured_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post("/api/mcp/servers/does-not-exist/reconnect")
        assert response.json() == {"error": "not found", "connected": False}


def test_get_mcp_servers_connected_reflects_a_real_live_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """connected must be a live signal (web/app.py's mcp_connections
    registry), not just "is it in mcp.json" -- add one server that stubs a
    successful connect and one that doesn't, and check GET /api/mcp/servers
    tells them apart."""

    class _FakeConnection:
        async def close(self) -> None:
            pass

    async def _fake_connect_returns_a_tool(name: str, config: Any) -> tuple[list[Any], Any, None]:
        from coscribe.runtime.types import tool_metadata

        def _tool(x: str = "") -> str:
            """fake"""
            return x

        _tool.__name__ = f"{name}__tool"
        tool_metadata(_tool, risk_category="READ", category=f"mcp:{name}")
        return [_tool], _FakeConnection(), None

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        # Stubbed to [] by _client_lg's own default -- never actually connects.
        client.post("/api/mcp/servers", json={"name": "flaky", "command": "uvx", "args": ["x"]})

        monkeypatch.setattr(
            "coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", _fake_connect_returns_a_tool
        )
        client.post("/api/mcp/servers", json={"name": "healthy", "command": "uvx", "args": ["y"]})

        servers = client.get("/api/mcp/servers").json()

    assert servers["flaky"]["connected"] is False
    assert servers["healthy"]["connected"] is True


def test_post_mcp_server_accepts_a_remote_server_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Settings > Connectors > Add > Add manually > Remote -- the frontend
    # form this backs was new work, not just a UI reskin: validate_mcp_
    # config (tools/mcp.py) already handled server_url/headers, but
    # POST /api/mcp/servers's own MCPServerUpdate model only ever accepted
    # command/args/env until now.
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/mcp/servers",
            json={
                "name": "remote-thing",
                "server_url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer token"},
            },
        )
        assert response.status_code == 200
        assert response.json()["rejected"] == {}

        mcp_config = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
        assert mcp_config["mcpServers"]["remote-thing"]["server_url"] == "https://example.com/mcp"

        servers = client.get("/api/mcp/servers").json()
        assert servers["remote-thing"]["server_url"] == "https://example.com/mcp"
        assert servers["remote-thing"]["masked_headers"]["Authorization"].endswith("oken")

        client.delete("/api/mcp/servers/remote-thing")


def test_post_mcp_server_stores_headers_via_keyring_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same guarantee as the identical env-var test above, for a remote
    # server's headers instead of a local server's env.
    fake_keyring = _install_fake_keyring(monkeypatch)
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        client.post(
            "/api/mcp/servers",
            json={
                "name": "remote-secret",
                "server_url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer real-secret"},
            },
        )

        raw = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
        assert raw["mcpServers"]["remote-secret"]["headers"] == {
            "Authorization": {"keyring_ref": "mcp:remote-secret:headers:Authorization"}
        }
        assert (
            fake_keyring.store[("coscribe", "mcp:remote-secret:headers:Authorization")]
            == "Bearer real-secret"
        )
        assert "real-secret" not in json.dumps(raw)

        client.delete("/api/mcp/servers/remote-secret")

    assert ("coscribe", "mcp:remote-secret:headers:Authorization") not in fake_keyring.store


def test_post_mcp_server_rejects_a_server_url_without_https(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/mcp/servers",
            json={"name": "bad-remote", "server_url": "ftp://example.com"},
        )

    assert response.status_code == 200
    assert "bad-remote" in response.json()["rejected"]


def test_lifespan_backgrounds_a_slow_mcp_connect_instead_of_blocking_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, user-reported bug: lifespan() used to
    `await connect_mcp_tools_lg(...)` directly, so the whole app -- not
    just one connector's own tools -- didn't start serving *anything*
    until every configured MCP server finished connecting. A slow one (a
    real Playwright launch is the worst case) meant a repeatable
    multi-minute wait on every single cold start, every time, regardless
    of which desktop shell was used. Fixed by bounding that wait
    (MCP_STARTUP_TIMEOUT_SECONDS) and letting a still-connecting server
    finish in the background instead, splicing its tools in once ready --
    the exact mechanism a live mid-conversation connector-add already
    uses (see test_post_mcp_server_splices_tools_into_both_new_and_
    already_open_sessions right below). Proves both halves: a session
    opened before the slow connect finishes works immediately with no
    wait (timed, not just "eventually passed"), and that same session
    picks up the tool once the background connect completes, with no
    reconnect needed."""
    from coscribe.runtime.types import tool_metadata

    def _fake_tool_fn(x: str = "") -> str:
        """A fake MCP tool for this test."""
        return f"fetched:{x}"

    _fake_tool_fn.__name__ = "fetch__fetch_url"
    tool_metadata(_fake_tool_fn, risk_category="READ", category="mcp:fetch")

    class _FakeConnection:
        async def close(self) -> None:
            pass

    async def _slow_connect_mcp_tools_lg(config_path: Path) -> tuple[list[Any], dict[str, Any]]:
        await asyncio.sleep(2.0)
        return [_fake_tool_fn], {"fetch": _FakeConnection()}

    monkeypatch.setattr("coscribe.web.app.MCP_STARTUP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("coscribe.runtime_lg.mcp.connect_mcp_tools_lg", _slow_connect_mcp_tools_lg)

    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}),
        encoding="utf-8",
    )
    call = _tool_call("call_1", "fetch__fetch_url", {"x": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, mcp_config_path=config_path) as client:
        # Opening the *first* session must not block on the still-
        # connecting "fetch" server -- a pre-fix lifespan() would have
        # hung inside `with _client_lg(...)`'s own context-manager entry
        # above (TestClient runs the ASGI lifespan synchronously) for the
        # full 2s fake-connect duration before this line was ever reached
        # at all. Timed, not just "it eventually passed" -- comfortably
        # under the 2s the fake connect takes (with real margin above
        # this harness's own baseline per-session overhead -- opening a
        # brand-new thread's WebSocket genuinely compiles a fresh
        # LangGraph graph, not instant even with no MCP involved at all)
        # proves startup itself wasn't the thing waiting on it.
        started = time.monotonic()
        with client.websocket_connect("/ws/t_startup_bg") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
        assert time.monotonic() - started < 1.0

        # Give the background connect (2s) time to actually finish.
        time.sleep(2.5)

        with client.websocket_connect("/ws/t_startup_bg") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "fetch hi"})
            messages = _receive_until(ws, "tasks_changed")

    tool_result = next(m for m in messages if m["type"] == "tool_result")
    assert tool_result["tool_name"] == "fetch__fetch_url"
    assert tool_result["result"] == "fetched:hi"


def test_post_mcp_server_splices_tools_into_both_new_and_already_open_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: a user enabled a
    connector mid-conversation and it had no effect on that same,
    already-open thread -- only a brand-new thread picked it up (matching
    what used to be the deliberate, documented gap in app.py's module
    docstring). Fixed via ChatSessionLG.refresh_extra_tools +
    _refresh_all_sessions_extra_tools, called after add/remove/bump-version
    below. This drives the "before" thread's ChatSessionLG into existence
    *before* the connector is added, then proves its *same* thread_id
    picks up the freshly-spliced tool on its very next message -- not just
    that a brand-new "after" thread does (also checked, same as before).
    Behavioral proof both times, not just a config-file check: has each
    session's model actually call the tool and confirms it executes (a
    real BaseTool the compiled graph genuinely knows about), not just that
    /api/mcp/servers reflects it."""
    from coscribe.runtime.types import tool_metadata

    def _fake_tool_fn(x: str = "") -> str:
        """A fake MCP tool for this test."""
        return f"fetched:{x}"

    _fake_tool_fn.__name__ = "fetch__fetch_url"
    tool_metadata(_fake_tool_fn, risk_category="READ", category="mcp:fetch")

    class _FakeConnection:
        async def close(self) -> None:
            pass

    async def _fake_connect_returns_a_tool(name: str, config: Any) -> tuple[list[Any], Any, None]:
        return [_fake_tool_fn], _FakeConnection(), None

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    call = _tool_call("call_1", "fetch__fetch_url", {"x": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        # Opened *before* the connector is added -- forces ChatSessionLG's
        # thread-a graph to be built with no "fetch" tool at all, so
        # picking it up below can only be explained by refresh_extra_tools,
        # not by the tool having been there all along.
        with client.websocket_connect("/ws/before") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history

        # Must be set *after* entering _client_lg -- its own body sets a
        # default stub for this same name on entry, which would otherwise
        # overwrite this one (bit me once already; see the surrounding
        # comment in _client_lg for why the default exists at all).
        monkeypatch.setattr(
            "coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", _fake_connect_returns_a_tool
        )
        response = client.post(
            "/api/mcp/servers",
            json={"name": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
        )
        assert response.json() == {"rejected": {}, "connected": True, "error": None}

        with client.websocket_connect("/ws/before") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "fetch hi"})
            before_messages = _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/after") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "fetch hi"})
            after_messages = _receive_until(ws, "tasks_changed")

    for messages in (before_messages, after_messages):
        tool_result = next(m for m in messages if m["type"] == "tool_result")
        assert tool_result["tool_name"] == "fetch__fetch_url"
        assert tool_result["result"] == "fetched:hi"


def test_delete_mcp_server_removes_only_that_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
                    "fs": {"command": "npx", "args": ["fs-server"]},
                }
            }
        ),
        encoding="utf-8",
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(
        tmp_path, monkeypatch, fake_model, mcp_config_path=config_path
    ) as client:
        response = client.delete("/api/mcp/servers/fetch")

    assert response.status_code == 200
    remaining = json.loads(config_path.read_text(encoding="utf-8"))
    assert set(remaining["mcpServers"]) == {"fs"}


def test_get_providers_catalog_returns_curated_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/providers/catalog")

    assert response.status_code == 200
    entries = {entry["name"]: entry for entry in response.json()}
    assert entries["anthropic"]["builtin"] is True
    assert entries["deepseek"]["builtin"] is False


def test_providers_catalog_includes_a_local_ollama_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """coscribe's whole pitch is local-first, but until now the provider
    catalog only offered cloud presets (deepseek/kimi/glm) -- no one-click
    entry for a fully offline model runtime. Ollama's own OpenAI-compatible
    server never checks the Authorization header, so unlike every other
    catalog entry its api_key is a placeholder, not a real secret; proves
    the round trip still works end to end with one anyway, since
    add_provider (unconditionally) and the frontend form both still
    require a non-blank value regardless of whether the provider itself
    cares."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        catalog = {entry["name"]: entry for entry in client.get("/api/providers/catalog").json()}
        assert catalog["ollama"]["builtin"] is False
        assert catalog["ollama"]["base_url"] == "http://localhost:11434/v1"

        response = client.post(
            "/api/providers",
            json={
                "name": "ollama",
                "base_url": catalog["ollama"]["base_url"],
                "api_key": "ollama",
                "default_model": "qwen2.5",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"restart_required": False, "rejected": {}}

        providers = client.get("/api/providers").json()
        assert providers["ollama"]["base_url"] == "http://localhost:11434/v1"
        assert providers["ollama"]["default_model"] == "qwen2.5"


def test_post_and_delete_provider_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/providers",
            json={
                "name": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-deepseek",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"restart_required": False, "rejected": {}}

        providers = client.get("/api/providers").json()
        assert providers["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
        assert providers["deepseek"]["masked_key"].endswith("eek")

        response = client.delete("/api/providers/deepseek")
        assert response.status_code == 200

        providers = client.get("/api/providers").json()
        assert "deepseek" not in providers


def test_get_providers_falls_back_to_the_global_default_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real bug, live-reported: a fresh install's COSCRIBE_GEMINI_DEFAULT_
    MODEL was never written (see test_setup_app.py's own fix note), so
    get_providers used to report an empty default_model for gemini even
    though it's the app's own active default provider -- ModelPicker.tsx
    filters out any entry with a blank default_model, so gemini would
    silently vanish from the switcher's dropdown the moment the user
    switched to a second, properly-configured provider and tried to
    switch back. Fixed by falling back to COSCRIBE_DEFAULT_MODEL's own
    model portion when the per-provider var is unset but that global
    default names this same provider."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "GEMINI_API_KEY='test-gemini-key'\n"
        "COSCRIBE_DEFAULT_MODEL='gemini:gemini-flash-latest'\n",
        encoding="utf-8",
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        providers = client.get("/api/providers").json()
        assert providers["gemini"]["default_model"] == "gemini-flash-latest"


def test_get_providers_prefers_the_per_provider_default_model_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "GEMINI_API_KEY='test-gemini-key'\n"
        "COSCRIBE_DEFAULT_MODEL='anthropic:claude-opus-5'\n"
        "COSCRIBE_GEMINI_DEFAULT_MODEL='gemini-2.5-pro'\n"
        "ANTHROPIC_API_KEY='sk-ant-test'\n",
        encoding="utf-8",
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        providers = client.get("/api/providers").json()
        assert providers["gemini"]["default_model"] == "gemini-2.5-pro"


def test_post_provider_persists_an_absolute_path_not_a_cwd_relative_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real bug, not hypothetical: add_provider's fallback path used to be
    a bare relative Path("./providers.json"), persisted verbatim into both
    settings.providers_config_path and .env. That's fine for the rest of
    *this* process's life only as long as cwd never changes again -- which
    it does, routinely, between test functions and (for a real deployment)
    between process restarts from a different working directory. Confirmed
    live: a real .env left over from an earlier manual run, holding that
    relative value, made an unrelated batch of tests fail with
    FileNotFoundError purely because they didn't all chdir the same way as
    the test that wrote it. Asserting the persisted value is absolute is
    what actually pins the fix -- a relative-but-different-looking string
    would pass a weaker "is not exactly 'providers.json'" check."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/providers",
            json={"name": "glm", "base_url": "https://open.bigmodel.cn/api/paas/v4",
                  "api_key": "sk-glm"},
        )
        assert response.status_code == 200

    persisted = dotenv_values(tmp_path / ".env")["COSCRIBE_PROVIDERS_CONFIG_PATH"]
    assert persisted is not None
    assert Path(persisted).is_absolute()
    assert Path(persisted) == tmp_path / "providers.json"


def test_post_provider_stores_via_keyring_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_keyring = _install_fake_keyring(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        client.post(
            "/api/providers",
            json={
                "name": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-deepseek-real",
            },
        )

        raw = json.loads((tmp_path / "providers.json").read_text(encoding="utf-8"))
        assert raw["providers"]["deepseek"]["api_key"] == {
            "keyring_ref": "custom-provider:deepseek"
        }
        assert fake_keyring.store[("coscribe", "custom-provider:deepseek")] == "sk-deepseek-real"

        # Masking still works correctly -- resolved via the keyring before
        # _mask ever sees it.
        providers = client.get("/api/providers").json()
        assert providers["deepseek"]["masked_key"].endswith("eal")

        client.delete("/api/providers/deepseek")

    assert ("coscribe", "custom-provider:deepseek") not in fake_keyring.store


def test_post_provider_builtin_key_stored_via_keyring_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_keyring = _install_fake_keyring(monkeypatch)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        client.post(
            "/api/providers",
            json={"name": "gemini", "base_url": "", "api_key": "sk-gemini-real"},
        )

        env_value = dotenv_values(tmp_path / ".env")["GEMINI_API_KEY"]
        assert env_value is not None
        assert env_value.startswith(secrets_module.ENV_KEYRING_PREFIX)
        assert "sk-gemini-real" not in env_value
        # The live process still gets the real value immediately, not the
        # sentinel -- no restart needed to use the key just entered.
        assert os.environ["GEMINI_API_KEY"] == "sk-gemini-real"

        providers = client.get("/api/providers").json()
        assert providers["gemini"]["masked_key"].endswith("eal")

    assert fake_keyring.store[("coscribe", "builtin-provider:GEMINI_API_KEY")] == "sk-gemini-real"


def test_provider_files_get_owner_only_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    os.chmod(tmp_path / ".env", 0o644)
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        client.post(
            "/api/providers",
            json={
                "name": "deepseek",
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-deepseek",
            },
        )
        client.post(
            "/api/providers",
            json={"name": "gemini", "base_url": "", "api_key": "sk-gemini"},
        )

    assert stat.S_IMODE((tmp_path / "providers.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_post_mcp_server_stores_env_via_keyring_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_keyring = _install_fake_keyring(monkeypatch)
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        client.post(
            "/api/mcp/servers",
            json={
                "name": "github-local",
                "command": "docker",
                "args": ["run", "github-mcp-server"],
                "env": {"GITHUB_TOKEN": "ghp_real_token"},
            },
        )

        raw = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
        assert raw["mcpServers"]["github-local"]["env"] == {
            "GITHUB_TOKEN": {"keyring_ref": "mcp:github-local:env:GITHUB_TOKEN"}
        }
        assert (
            fake_keyring.store[("coscribe", "mcp:github-local:env:GITHUB_TOKEN")]
            == "ghp_real_token"
        )

        servers = client.get("/api/mcp/servers").json()
        assert servers["github-local"]["masked_env"]["GITHUB_TOKEN"].endswith("ken")

        client.delete("/api/mcp/servers/github-local")

    assert ("coscribe", "mcp:github-local:env:GITHUB_TOKEN") not in fake_keyring.store


def test_mcp_json_file_gets_owner_only_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        client.post(
            "/api/mcp/servers",
            json={"name": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
        )

    assert stat.S_IMODE((tmp_path / "mcp.json").stat().st_mode) == 0o600


def _network_reachable() -> bool:
    """The script-env package endpoints need pypi.org for their first real
    call (creating the venv seeds baseline packages) -- skip cleanly
    offline, same reasoning as test_script_env.py's identical helper."""
    try:
        socket.create_connection(("pypi.org", 443), timeout=5).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _network_reachable(), reason="pypi.org not reachable from this environment")
def test_script_env_package_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        # First call creates the venv and seeds baseline packages.
        baseline = client.get("/api/script-env/packages").json()
        baseline_names = {pkg["name"].lower() for pkg in baseline}
        assert "openpyxl" in baseline_names

        response = client.post("/api/script-env/packages", json={"package": "six"})
        assert response.status_code == 200
        assert response.json() == {"success": True, "error": None}

        packages = client.get("/api/script-env/packages").json()
        assert "six" in {pkg["name"].lower() for pkg in packages}

        response = client.delete("/api/script-env/packages/six")
        assert response.status_code == 200
        assert response.json()["success"] is True

        packages = client.get("/api/script-env/packages").json()
        assert "six" not in {pkg["name"].lower() for pkg in packages}


def test_post_script_env_package_rejects_blank_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post("/api/script-env/packages", json={"package": "  "})

    assert response.status_code == 200
    assert response.json()["success"] is False


def test_get_script_env_interpreter_starts_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/script-env/interpreter")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is None
    assert sys.executable in body["auto_detected"]


def test_set_script_env_interpreter_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post("/api/script-env/interpreter", json={"path": sys.executable})
        assert response.status_code == 200
        assert response.json() == {"success": True, "error": None}

        info = client.get("/api/script-env/interpreter").json()
        assert info["configured"] == sys.executable

        cleared = client.post("/api/script-env/interpreter", json={"path": ""})
        assert cleared.json() == {"success": True, "error": None}

        info_after_clear = client.get("/api/script-env/interpreter").json()
        assert info_after_clear["configured"] is None


def test_set_script_env_interpreter_rejects_a_bad_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/script-env/interpreter", json={"path": str(tmp_path / "not-a-real-interpreter")}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is False
    assert body["error"]


def test_script_env_package_install_does_not_block_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: this endpoint used
    to call install_package directly inside its `async def` handler, which
    runs the blocking subprocess.run wait *on the single asyncio event
    loop* -- freezing every other request this process serves for as long
    as pip takes, not just this one response. Live symptom: after setting
    a new interpreter override (which deletes the existing venv, see
    set_interpreter_override) and clicking Add on a package, the whole
    app looked dead -- Settings' other tabs came back empty, and a chat
    message sent over the WebSocket got no response at all.

    Proven here by making install_package artificially slow (a plain
    time.sleep, standing in for a real multi-minute pip subprocess) and
    confirming a concurrent, unrelated request still completes quickly
    instead of queuing behind it -- only possible if the slow call is
    actually offloaded to a thread (asyncio.to_thread) rather than
    awaited inline on the same loop. Uses client.portal (the anyio
    BlockingPortal TestClient itself runs the app's event loop through --
    see test_wake_poll_loop_publishes_background_events_for_fired_wakes_
    and_triggers' docstring for the same idiom) to genuinely run the slow
    call concurrently with a normal client.get, rather than TestClient's
    usual one-request-at-a-time synchronous dispatch."""

    def _slow_install(state_dir: Path, name: str) -> dict[str, object]:
        time.sleep(0.5)
        return {"success": True, "error": None}

    monkeypatch.setattr("coscribe.web.app.install_package", _slow_install)
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        route = next(
            r
            for r in client.app.routes
            if getattr(r, "path", None) == "/api/script-env/packages"
            and "POST" in (r.methods or set())
        )
        start = time.monotonic()
        slow_future = client.portal.start_task_soon(
            route.endpoint, ScriptEnvPackageInstall(package="six")
        )
        time.sleep(0.05)  # let the slow call actually start before racing it
        fast_response = client.get("/api/tools")
        fast_elapsed = time.monotonic() - start
        slow_result = slow_future.result(timeout=5)

    assert fast_response.status_code == 200
    assert fast_elapsed < 0.4  # well under the 0.5s sleep -- it wasn't queued behind it
    assert slow_result == {"success": True, "error": None}


def _node_npm_available_and_reachable() -> bool:
    """Mirrors _network_reachable above, plus node/npm actually being
    installed -- unlike Python (coscribe's own runtime, always present),
    Node is a genuinely optional system dependency (see tools/node_env.py)
    that may not exist in every CI environment."""
    if shutil.which("node") is None or shutil.which("npm") is None:
        return False
    try:
        socket.create_connection(("registry.npmjs.org", 443), timeout=5).close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(
    not _node_npm_available_and_reachable(),
    reason="node/npm not installed, or registry.npmjs.org not reachable from this environment",
)
def test_node_env_package_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        # First call creates node-env and seeds pptxgenjs.
        baseline = client.get("/api/node-env/packages").json()
        baseline_names = {pkg["name"].lower() for pkg in baseline}
        assert "pptxgenjs" in baseline_names

        response = client.post("/api/node-env/packages", json={"package": "left-pad"})
        assert response.status_code == 200
        assert response.json() == {"success": True, "error": None}

        packages = client.get("/api/node-env/packages").json()
        assert "left-pad" in {pkg["name"].lower() for pkg in packages}

        response = client.delete("/api/node-env/packages/left-pad")
        assert response.status_code == 200
        assert response.json()["success"] is True

        packages = client.get("/api/node-env/packages").json()
        assert "left-pad" not in {pkg["name"].lower() for pkg in packages}


def test_post_node_env_package_rejects_blank_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post("/api/node-env/packages", json={"package": "  "})

    assert response.status_code == 200
    assert response.json()["success"] is False


def test_get_skills_lists_the_builtin_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills")

    assert response.status_code == 200
    names = {s["name"] for s in response.json()}
    assert names == {"PPTX Slides", "Excel Spreadsheets", "Word Documents", "Skill Creator"}


def test_get_skills_tags_builtin_vs_custom_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "skills" / "mine").mkdir(parents=True)
    (tmp_path / "skills" / "mine" / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nbody", encoding="utf-8"
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills")

    by_name = {s["name"]: s["source"] for s in response.json()}
    assert by_name["PPTX Slides"] == "builtin"
    assert by_name["mine"] == "custom"


def test_get_skill_files_lists_a_builtin_skills_real_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from urllib.parse import quote

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get(f"/api/skills/{quote('PPTX Slides')}/files")

    assert response.status_code == 200
    assert "SKILL.md" in response.json()["files"]


def test_get_skill_files_lists_nested_paths_for_a_custom_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "skills" / "mine"
    (skill_dir / "reference").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nbody", encoding="utf-8"
    )
    (skill_dir / "reference" / "notes.md").write_text("some notes", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/mine/files")

    assert response.status_code == 200
    assert sorted(response.json()["files"]) == ["SKILL.md", "reference/notes.md"]


def test_get_skill_files_unknown_skill_name_404s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/no-such-skill/files")

    assert response.status_code == 404


def test_get_skill_file_content_returns_the_real_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "skills" / "mine"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nreal body text", encoding="utf-8"
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/mine/files/SKILL.md")

    assert response.status_code == 200
    body = response.json()
    assert body["path"] == "SKILL.md"
    assert "real body text" in body["content"]


def test_get_skill_file_content_rejects_a_path_traversal_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "skills" / "mine"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nbody", encoding="utf-8"
    )
    # A real secret file the traversal attempt tries to escape the skill's
    # own directory to reach -- state_dir is a sibling of skills_dir, both
    # directly under tmp_path (see _client_lg's own settings construction).
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "secret.txt").write_text("do not leak this", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/mine/files/../../state/secret.txt")

    assert response.status_code in (400, 404)
    assert "do not leak this" not in response.text


def test_get_skill_file_content_missing_file_404s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_dir = tmp_path / "skills" / "mine"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: mine\ndescription: my own skill\n---\nbody", encoding="utf-8"
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/mine/files/no-such-file.md")

    assert response.status_code == 404


def test_get_skill_file_content_unknown_skill_name_404s(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/skills/no-such-skill/files/SKILL.md")

    assert response.status_code == 404


def test_upload_skill_md_appears_in_get_skills_without_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The real regression this guards: skills_by_name used to be a
    # closure snapshot taken once at startup (same staleness shape as
    # switch_model's custom-providers bug) -- an uploaded skill wouldn't
    # show up in GET /api/skills, or be acceptable to select_skills,
    # until the process restarted.
    fake_model = FakeToolCallingChatModel(responses=[])
    md_content = "---\nname: mine\ndescription: my own skill\n---\nbody"
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        upload = client.post(
            "/api/skills/upload",
            files={"file": ("mine.md", md_content, "text/markdown")},
        )
        assert upload.status_code == 200
        assert upload.json() == {"name": "mine", "description": "my own skill", "source": "custom"}

        response = client.get("/api/skills")
        names = {s["name"] for s in response.json()}
        assert "mine" in names

        with client.websocket_connect("/ws/t_upload_skill") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "select_skills", "skills": ["mine"]})
            updated_state = ws.receive_json()
    assert updated_state["enabled_skills"] == ["mine"]


def test_upload_skill_rejects_malformed_skill_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/skills/upload",
            files={"file": ("bad.md", "not even frontmatter", "text/markdown")},
        )

    assert response.status_code == 400
    assert "frontmatter" in response.json()["error"]


def test_upload_skill_rejects_unsupported_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/skills/upload",
            files={"file": ("notes.txt", "whatever", "text/plain")},
        )

    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["error"]


def test_new_thread_defaults_to_the_builtin_skills_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_skills_default") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history

    assert sorted(state["enabled_skills"]) == [
        "Excel Spreadsheets",
        "PPTX Slides",
        "Skill Creator",
        "Word Documents",
    ]


def test_select_skills_ws_message_enables_them_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_select_skills") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history

            ws.send_json({"type": "select_skills", "skills": ["PPTX Slides", "Excel Spreadsheets"]})
            updated_state = ws.receive_json()

    assert sorted(updated_state["enabled_skills"]) == ["Excel Spreadsheets", "PPTX Slides"]
    sidecar = tmp_path / "state" / "t_select_skills.skills"
    assert sorted(json.loads(sidecar.read_text(encoding="utf-8"))) == [
        "Excel Spreadsheets",
        "PPTX Slides",
    ]


def test_select_skills_can_be_toggled_more_than_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_skills_retoggle") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history

            ws.send_json({"type": "select_skills", "skills": ["PPTX Slides"]})
            first = ws.receive_json()
            ws.send_json({"type": "select_skills", "skills": []})
            second = ws.receive_json()

    assert first["enabled_skills"] == ["PPTX Slides"]
    assert second["enabled_skills"] == []


def test_select_skills_unknown_name_is_dropped_not_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_skills_unknown") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history

            ws.send_json({"type": "select_skills", "skills": ["PPTX Slides", "nope"]})
            updated_state = ws.receive_json()

    assert updated_state["enabled_skills"] == ["PPTX Slides"]


def test_skills_query_param_resolves_enabled_skills_on_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_skills_qs?skills=PPTX%20Slides") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history

    assert state["enabled_skills"] == ["PPTX Slides"]


def test_skills_choice_persists_across_reconnect_without_the_query_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as first_process:
        with first_process.websocket_connect("/ws/t_skills_persist?skills=Word%20Documents") as ws:
            ws.receive_json()
            ws.receive_json()  # history

    # Fresh create_app_lg() -- its own empty in-memory `sessions` dict, same
    # tmp_path on disk -- so a reconnect with no ?skills= this time can only
    # pick the enabled set back up from the sidecar, not in-process state.
    with _client_lg(tmp_path, monkeypatch, fake_model) as second_process:
        with second_process.websocket_connect("/ws/t_skills_persist") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history

    assert state["enabled_skills"] == ["Word Documents"]


def test_delete_thread_removes_skills_sidecar_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_skills_delete?skills=PPTX%20Slides") as ws:
            ws.receive_json()
            ws.receive_json()  # history
        sidecar = tmp_path / "state" / "t_skills_delete.skills"
        assert sidecar.is_file()

        client.delete("/api/threads/t_skills_delete")

        assert not sidecar.is_file()


def test_workspace_query_param_resolves_workspace_on_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = tmp_path / "project-a"
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect(f"/ws/t_ws_qs?workspace={chosen}") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history

    assert state["workspace_root"] == str(chosen)
    assert state["workspace_explicit"] is True
    assert state["folders"] == [str(chosen)]
    sidecar = tmp_path / "state" / "t_ws_qs.workspace"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == [str(chosen)]


def test_new_thread_without_workspace_falls_back_to_settings_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_ws_default") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history

    assert state["workspace_root"] == str(tmp_path / "workspace")
    assert state["workspace_explicit"] is False
    assert not (tmp_path / "state" / "t_ws_default.workspace").exists()


def test_workspace_choice_persists_across_reconnect_without_the_query_param(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = tmp_path / "project-b"
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as first_process:
        with first_process.websocket_connect(f"/ws/t_ws_persist?workspace={chosen}") as ws:
            ws.receive_json()
            ws.receive_json()  # history

    # Fresh create_app_lg() -- empty in-memory sessions dict, same tmp_path
    # on disk -- so a reconnect with no ?workspace= this time can only pick
    # the choice back up from the sidecar, not in-process state.
    with _client_lg(tmp_path, monkeypatch, fake_model) as second_process:
        with second_process.websocket_connect("/ws/t_ws_persist") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history

    assert state["workspace_root"] == str(chosen)


def test_set_folders_changes_a_conversations_folders_any_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "project-first"
    second = tmp_path / "project-second"
    first.mkdir()
    second.mkdir()
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_ws_folders") as ws:
            ws.receive_json()  # state (falls back to settings.workspace_root)
            ws.receive_json()  # history

            ws.send_json({"type": "set_folders", "folders": [str(first)]})
            one = ws.receive_json()
            ws.send_json({"type": "set_folders", "folders": [str(first), str(second)]})
            two = ws.receive_json()
            ws.send_json({"type": "set_folders", "folders": [str(second)]})
            swapped = ws.receive_json()

    assert one["workspace_root"] == str(first)
    assert one["workspace_explicit"] is True
    assert two["folders"] == [str(first), str(second)]
    assert swapped["workspace_root"] == str(second)
    assert swapped["folders"] == [str(second)]
    sidecar = tmp_path / "state" / "t_ws_folders.workspace"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == [str(second)]


def test_removing_every_folder_returns_to_the_default_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = tmp_path / "project-gone"
    chosen.mkdir()
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect(f"/ws/t_ws_clear?workspace={chosen}") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "set_folders", "folders": []})
            cleared = ws.receive_json()

    assert cleared["folders"] == []
    assert cleared["workspace_explicit"] is False
    assert cleared["workspace_root"] == str(tmp_path / "workspace")
    assert not (tmp_path / "state" / "t_ws_clear.workspace").exists()


def test_a_folder_that_does_not_exist_is_refused_and_nothing_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = tmp_path / "project-kept"
    kept.mkdir()
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect(f"/ws/t_ws_bad?workspace={kept}") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            missing = tmp_path / "missing"
            ws.send_json({"type": "set_folders", "folders": [str(kept), str(missing)]})
            refused = ws.receive_json()

    assert refused["type"] == "error"
    assert "isn't an existing folder" in refused["message"]
    sidecar = tmp_path / "state" / "t_ws_bad.workspace"
    assert json.loads(sidecar.read_text(encoding="utf-8")) == [str(kept)]


def test_a_single_path_sidecar_from_before_multiple_folders_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = tmp_path / "project-legacy"
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "t_ws_legacy.workspace").write_text(str(chosen), encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_ws_legacy") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history

    assert state["folders"] == [str(chosen)]


def test_the_agent_can_write_into_a_conversations_second_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = tmp_path / "project-main"
    extra = tmp_path / "project-extra"
    main.mkdir()
    extra.mkdir()
    call = _tool_call("call_1", "write_file", {"path": str(extra / "note.txt"), "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_ws_extra") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "set_folders", "folders": [str(main), str(extra)]})
            ws.receive_json()  # state
            ws.send_json({"type": "user_message", "text": "write hi into the second folder"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

    assert (extra / "note.txt").read_text() == "hi"


def test_delete_thread_removes_workspace_sidecar_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = tmp_path / "project-d"
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect(f"/ws/t_ws_delete?workspace={chosen}") as ws:
            ws.receive_json()
            ws.receive_json()  # history
        sidecar = tmp_path / "state" / "t_ws_delete.workspace"
        assert sidecar.is_file()

        client.delete("/api/threads/t_ws_delete")

        assert not sidecar.is_file()


def test_write_file_lands_in_the_threads_own_workspace_not_the_global_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof that a per-thread workspace_root actually reaches
    the tool layer (coordinator.py's build_coordinator_agent -- not just
    that the WS "state" event reports the right string, which the other
    workspace tests above already cover)."""
    custom_workspace = tmp_path / "custom-project"
    custom_workspace.mkdir()
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect(f"/ws/t_ws_write?workspace={custom_workspace}") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})

            approval = ws.receive_json()
            assert approval["type"] == "approval_required"

            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

    assert (custom_workspace / "note.txt").read_text() == "hi"
    assert not (tmp_path / "workspace" / "note.txt").exists()


def test_get_threads_endpoint_includes_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = tmp_path / "project-e"
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="hi"), AIMessage(content="hi again")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect(f"/ws/t_ws_list_a?workspace={chosen}") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/t_ws_list_b") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello again"})
            _receive_until(ws, "tasks_changed")

        threads = {t["thread_id"]: t for t in client.get("/api/threads").json()}

    assert threads["t_ws_list_a"]["workspace_root"] == str(chosen)
    assert threads["t_ws_list_b"]["workspace_root"] == str(tmp_path / "workspace")


def test_get_scheduled_tasks_endpoint_lists_saved_triggers_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTrigger, ScheduledTriggerStore, ScheduleRule

    store = ScheduledTriggerStore(tmp_path / "state")
    store.save(
        ScheduledTrigger(
            trigger_id="trig-1",
            name="Daily standup notes",
            schedule=ScheduleRule(kind="daily", at="09:00"),
            enabled=True,
            created_at="2026-01-01T00:00:00",
            next_run_at="2026-01-02T09:00:00",
            prompt="Summarize yesterday",
        )
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/scheduled-tasks")

    assert response.status_code == 200
    [trigger] = response.json()
    assert trigger["trigger_id"] == "trig-1"
    assert trigger["name"] == "Daily standup notes"


def _wait_for_run_status(
    client: Any, trigger_id: str, run_id: str, timeout: float = 5.0
) -> dict[str, Any]:
    deadline = time.time() + timeout
    while True:
        tasks = client.get("/api/scheduled-tasks").json()
        [task] = [t for t in tasks if t["trigger_id"] == trigger_id]
        [run] = [r for r in task["runs"] if r["run_id"] == run_id]
        if run["status"] != "running" or time.time() > deadline:
            return run
        time.sleep(0.05)


def test_run_now_returns_at_once_and_the_run_finishes_in_the_background_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="report written")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "Report", "kind": "manual", "at": "", "prompt": "Write the report"},
        ).json()
        response = client.post(f"/api/scheduled-tasks/{created['trigger_id']}/run")
        body = response.json()
        run = _wait_for_run_status(client, created["trigger_id"], body["run"]["run_id"])

        with client.websocket_connect(f"/ws/{body['run']['thread_id']}") as ws:
            state = ws.receive_json()
            history = ws.receive_json()

    assert response.status_code == 200
    assert body["run"]["status"] == "running"
    assert body["run"]["source"] == "manual"
    assert run["status"] == "completed"
    assert state["turn_in_flight"] is False
    assert history["entries"][0]["kind"] == "user"
    assert history["entries"][0]["text"].startswith('[Scheduled run of "Report"')
    assert history["entries"][-1] == {"kind": "agent", "text": "report written"}


def test_deleting_a_task_deletes_its_run_conversations_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="done")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "Temp", "kind": "manual", "at": "", "prompt": "Do it"},
        ).json()
        run = client.post(f"/api/scheduled-tasks/{created['trigger_id']}/run").json()["run"]
        _wait_for_run_status(client, created["trigger_id"], run["run_id"])

        assert client.delete(f"/api/scheduled-tasks/{created['trigger_id']}").status_code == 200
        with client.websocket_connect(f"/ws/{run['thread_id']}") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert history["entries"] == []


def test_startup_sweeps_run_conversations_no_task_lists_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task deleted without a checkpointer at hand (the model's
    delete_scheduled_task) leaves its run conversations behind; the next
    startup removes them and keeps every run a task still lists."""
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="done"), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        runs = {}
        for name in ("Keep", "Drop"):
            created = client.post(
                "/api/scheduled-tasks",
                json={"name": name, "kind": "manual", "at": "", "prompt": "Do it"},
            ).json()
            run = client.post(f"/api/scheduled-tasks/{created['trigger_id']}/run").json()["run"]
            _wait_for_run_status(client, created["trigger_id"], run["run_id"])
            runs[name] = (created["trigger_id"], run["thread_id"])
    ScheduledTriggerStore(tmp_path / "state").delete(runs["Drop"][0])

    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        histories = {}
        for name, (_, thread_id) in runs.items():
            with client.websocket_connect(f"/ws/{thread_id}") as ws:
                ws.receive_json()  # state
                histories[name] = ws.receive_json()["entries"]

    assert histories["Keep"] != []
    assert histories["Drop"] == []


def test_scheduled_task_notes_endpoints_lg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coscribe.tools.scheduled_tasks import MAX_NOTES_CHARS

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "N", "kind": "manual", "at": "", "prompt": "p"},
        ).json()
        url = f"/api/scheduled-tasks/{created['trigger_id']}/notes"
        empty = client.get(url).json()
        saved = client.put(url, json={"notes": "  left off at page 3  "}).json()
        too_long = client.put(url, json={"notes": "x" * (MAX_NOTES_CHARS + 1)})
        missing = client.get("/api/scheduled-tasks/nope/notes")

    assert created["notes_enabled"] is True
    assert empty == {"notes": ""}
    assert saved == {"notes": "left off at page 3"}
    assert too_long.status_code == 400
    assert missing.status_code == 404


def test_create_scheduled_task_is_a_draft_the_user_reviews_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model's create_scheduled_task call never creates anything
    itself: the user gets the draft, saves (or dismisses) it from the UI,
    and their answer becomes the tool's result."""
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    draft_args = {"name": "Weekly export", "kind": "weekly", "at": "09:00", "prompt": "Export"}
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="", tool_calls=[_tool_call("d1", "create_scheduled_task", draft_args)]
            ),
            AIMessage(content="Saved it."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_draft") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "make this weekly"})
            draft = _receive_until(ws, "task_draft_required")[-1]
            ws.send_json(
                {
                    "type": "question_response",
                    "id": draft["id"],
                    "answer": 'Saved as scheduled task "Weekly export".',
                }
            )
            messages = _receive_until(ws, "tasks_changed")

    assert draft["draft"] == draft_args
    assert not any(m["type"] == "tool_result" for m in messages)
    tool_message = next(m for m in fake_model.received[-1] if isinstance(m, ToolMessage))
    assert tool_message.content == 'Saved as scheduled task "Weekly export".'
    assert ScheduledTriggerStore(tmp_path / "state").list_all() == []


def test_saveworkflow_asks_the_model_to_draft_a_task_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="drafting")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_saveworkflow") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/saveworkflow"})
            usage = ws.receive_json()
            ws.send_json({"type": "user_message", "text": "/saveworkflow Monthly close"})
            _receive_until(ws, "tasks_changed")

    assert usage == {"type": "error", "message": "Usage: /saveworkflow <name>"}
    [human] = [m for m in fake_model.received[-1] if isinstance(m, HumanMessage)]
    assert 'named "Monthly close"' in str(human.content)
    assert "create_scheduled_task" in str(human.content)


def test_create_scheduled_task_endpoint_persists_a_prompt_backed_trigger_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/scheduled-tasks",
            json={
                "name": "Daily standup notes",
                "kind": "daily",
                "at": "09:00",
                "prompt": "Summarize yesterday",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Daily standup notes"
    assert body["enabled"] is True
    resolved = ScheduledTriggerStore(tmp_path / "state").load(body["trigger_id"])
    assert resolved is not None
    assert resolved.prompt == "Summarize yesterday"


def test_create_scheduled_task_endpoint_rejects_invalid_payload_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/scheduled-tasks",
            json={"name": "x", "kind": "daily", "at": "09:00", "prompt": "  "},
        )

    assert response.status_code == 400
    assert "error" in response.json()


def test_pause_and_resume_scheduled_task_endpoints_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "x", "kind": "daily", "at": "09:00", "prompt": "p"},
        ).json()

        paused = client.post(f"/api/scheduled-tasks/{created['trigger_id']}/pause")
        assert paused.status_code == 200
        assert paused.json()["enabled"] is False

        resumed = client.post(f"/api/scheduled-tasks/{created['trigger_id']}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["enabled"] is True

    assert ScheduledTriggerStore(tmp_path / "state").load(created["trigger_id"]) is not None


def test_pause_scheduled_task_endpoint_unknown_id_404s_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post("/api/scheduled-tasks/nope/pause")

    assert response.status_code == 404


def test_delete_scheduled_task_endpoint_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "x", "kind": "daily", "at": "09:00", "prompt": "p"},
        ).json()

        response = client.delete(f"/api/scheduled-tasks/{created['trigger_id']}")

    assert response.status_code == 200
    assert response.json() == {"deleted": created["trigger_id"]}
    assert ScheduledTriggerStore(tmp_path / "state").load(created["trigger_id"]) is None


def test_delete_scheduled_task_endpoint_unknown_id_404s_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.delete("/api/scheduled-tasks/nope")

    assert response.status_code == 404


def test_saveskill_writes_skill_md_and_makes_it_usable_immediately_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/saveskill's happy path: one curator call proposes description+body
    (name always stays whatever the user typed), "yes" confirms, and the file
    actually lands at <skills_dir>/<slug>/SKILL.md with valid frontmatter.
    Also proves the real point of set_enabled_skills' own skills_by_slug
    refresh: /<slug> force-loads the brand-new skill in this *same*
    session right after saving it, no reconnect needed."""
    curator_decision = json.dumps(
        {
            "decision": "propose",
            "description": "Load when asked to write the weekly status report.",
            "body": "1. Pull last week's numbers.\n2. Summarize wins and blockers.",
        }
    )
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="here's this week's report"),
            AIMessage(content=curator_decision),
            AIMessage(content="following the skill now"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_sk1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write the weekly status report"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/saveskill Weekly Report Format"})
            preview = ws.receive_json()
            assert preview["type"] == "agent_message"
            assert "weekly-report-format" in preview["text"]
            assert "Pull last week's numbers" in preview["text"]
            ws.send_json({"type": "user_message", "text": "yes"})
            saved = ws.receive_json()
            assert saved == {
                "type": "skill_saved",
                "name": "Weekly Report Format",
                "slug": "weekly-report-format",
            }
            state = ws.receive_json()
            assert state["type"] == "state"
            assert "Weekly Report Format" in state["enabled_skills"]

            # /<slug> force-loads it right away, same session, no reconnect.
            ws.send_json({"type": "user_message", "text": "/weekly-report-format go"})
            _receive_until(ws, "tasks_changed")

    skill_path = tmp_path / "skills" / "weekly-report-format" / "SKILL.md"
    assert skill_path.is_file()
    text = skill_path.read_text(encoding="utf-8")
    assert "name: Weekly Report Format" in text
    assert "description: Load when asked to write the weekly status report." in text
    assert "Pull last week's numbers" in text

    assert len(fake_model.received) == 3
    human_messages = [m for m in fake_model.received[-1] if isinstance(m, HumanMessage)]
    assert "Skill 'Weekly Report Format' invoked directly via /weekly-report-format" in (
        human_messages[-1].content
    )


def test_saveskill_asks_a_clarifying_question_when_curator_is_unsure_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    curator_decision = json.dumps(
        {"decision": "clarify", "question": "What should this skill teach?"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="ok, done"), AIMessage(content=curator_decision)]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_sk2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "did a bunch of unrelated stuff"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/saveskill mystery"})
            question = ws.receive_json()

    assert question == {"type": "agent_message", "text": "What should this skill teach?"}
    assert not (tmp_path / "skills" / "mystery").exists()


def test_saveskill_refuses_to_shadow_a_builtin_skill_slug_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slug colliding with a built-in (e.g. "word", from load_builtin_
    skills()) would silently shadow it for every future /<slug> lookup --
    skills_by_slug keeps whichever of load_builtin_skills()/load_skills()
    is scanned last for a repeated key. Refused outright rather than
    silently letting a new local skill hijack a built-in's command."""
    curator_decision = json.dumps(
        {"decision": "propose", "description": "Load whenever.", "body": "Do the thing."}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="ok"), AIMessage(content=curator_decision)]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_sk3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hi"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/saveskill Word"})
            preview = ws.receive_json()
            assert preview["type"] == "agent_message"
            ws.send_json({"type": "user_message", "text": "yes"})
            error = ws.receive_json()

    assert error["type"] == "error"
    assert "/word is already a built-in skill" in error["message"]
    assert not (tmp_path / "skills" / "word").exists()


def test_saveskill_discarded_on_a_non_yes_answer_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    curator_decision = json.dumps(
        {"decision": "propose", "description": "Load whenever.", "body": "Do the thing."}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="ok"), AIMessage(content=curator_decision)]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_sk4") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hi"})
            _receive_until(ws, "tasks_changed")

            ws.send_json({"type": "user_message", "text": "/saveskill throwaway"})
            ws.receive_json()  # preview
            ws.send_json({"type": "user_message", "text": "no thanks"})
            discarded = ws.receive_json()

    assert discarded["type"] == "agent_message"
    assert "Discarded" in discarded["text"]
    assert not (tmp_path / "skills" / "throwaway").exists()


def test_tool_raising_a_plain_exception_is_reported_to_the_model_not_a_crashed_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: create_agent's
    ToolNode only converts a tool's raised exception into an error
    ToolMessage for its own ToolInvocationError -- a plain ValueError
    (exactly what every built-in coscribe tool raises for an ordinary
    "bad input" case, e.g. read_file's "File does not exist: ...") used to
    propagate all the way out of astream()/ainvoke(), hit
    _handle_user_message_locked's except Exception, and end the turn dead
    with a bare {"type": "error"} message -- no agent_message, no
    tasks_changed, indistinguishable from the turn hanging forever. Fixed
    by _CatchToolErrorsMiddleware (runtime_lg/agent.py) -- this proves the
    turn now completes normally instead, with the model seeing the error
    and getting to respond."""
    call = _tool_call("call_1", "read_file", {"path": "sap_login.md"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="sorry, that file doesn't exist"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_tool_error") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "read sap_login.md"})
            messages = _receive_until(ws, "tasks_changed")

    assert not any(m["type"] == "error" for m in messages)
    tool_result = next(m for m in messages if m["type"] == "tool_result")
    assert tool_result["tool_name"] == "read_file"
    assert "File does not exist" in str(tool_result["result"])
    # This same ToolMessage.status the middleware sets to "error" (see this
    # test's own docstring) is also what the transcript's failure styling
    # keys off -- see wire.ts's ToolResultEvent.is_error.
    assert tool_result["is_error"] is True
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "sorry, that file doesn't exist"


def test_get_threads_endpoint_lists_saved_threads_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: list_threads used to
    glob settings.state_dir for *.json files -- the old runtime's storage
    shape, not this one's. runtime_lg persists conversation history in the
    shared AsyncSqliteSaver checkpointer, so that glob always came back
    empty and the session-switcher UI never showed any history, even
    though every thread's real state was sitting right there in the
    checkpoint database."""
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_list1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        response = client.get("/api/threads")

    assert response.status_code == 200
    threads = {t["thread_id"]: t for t in response.json()}
    assert "t_list1" in threads
    entry = threads["t_list1"]
    assert entry["preview"] == "hello"
    assert entry["message_count"] == 2  # the human "hello" + the AI's "hi" reply
    assert entry["updated_at"]  # a real ISO timestamp, not asserting the exact value


def test_get_threads_endpoint_strips_mode_note_and_sorts_by_recency_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Covers strip_mode_note (a live turn's mode-note prefix, e.g.
    "[normal mode: ...] ", shouldn't leak into the preview text a user
    never actually typed) and the most-recently-updated-first sort."""
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="hi"), AIMessage(content="hi again")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_older") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "first thread"})
            _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/t_newer") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "second thread"})
            _receive_until(ws, "tasks_changed")

        threads = client.get("/api/threads").json()

    assert [t["thread_id"] for t in threads] == ["t_newer", "t_older"]
    newer = threads[0]
    assert not newer["preview"].startswith("[")
    assert "second thread" in newer["preview"]


def test_get_threads_endpoint_excludes_scheduled_task_threads_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fired Scheduled Task's own dedicated conversation (thread_id
    "scheduled-<trigger_id>", see tools/scheduled_tasks.py's
    SCHEDULED_THREAD_PREFIX) has its own surface in the frontend's
    Scheduled section -- it must not also show up in the ordinary chat
    sidebar's session list once its first turn writes a checkpoint,
    per an explicit request that the two stay separate."""
    # One response per turn -- two separate websocket connections below
    # each run their own turn against the same fake_model.
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="hi"), AIMessage(content="hi again")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_ordinary") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/scheduled-abc123") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "do it"})
            _receive_until(ws, "tasks_changed")

        thread_ids = {t["thread_id"] for t in client.get("/api/threads").json()}

    assert "t_ordinary" in thread_ids
    assert "scheduled-abc123" not in thread_ids


def test_date_note_is_per_turn_message_content_not_baked_into_the_system_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Caching regression test: the date note used to be glued onto the
    very front of the system prompt (recomputed only when the agent got
    rebuilt), which poisoned the entire prefix for any provider's
    prefix-based caching -- Anthropic cache_control, and just as much
    Gemini/OpenAI-compatible providers' automatic caching, no
    cache_control needed on their end for the damage to apply. Fixed by
    moving it into per-turn message content instead (see
    current_date_note's docstring in runtime_lg/messages.py) -- this
    tests both halves: the system prompt sent to the model stays frozen
    (no date in it), and the actual HumanMessage the model sees each turn
    still carries the date, so real-world grounding isn't lost."""
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi there!")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_date_note") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

    assert len(fake_model.received) == 1
    sent_messages = fake_model.received[0]
    system_messages = [m for m in sent_messages if isinstance(m, SystemMessage)]
    assert system_messages, "expected a system message in the request"
    assert "Today's real date" not in system_messages[0].content

    human_messages = [m for m in sent_messages if isinstance(m, HumanMessage)]
    assert human_messages, "expected a human message in the request"
    today = datetime.now().strftime("%Y-%m-%d")
    assert human_messages[-1].content.startswith(f"Today's real date is {today} (")


def test_delete_thread_endpoint_removes_checkpoints_and_tasks_sidecar_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.tasks import TaskToolkit

    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_del1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        TaskToolkit("t_del1", tmp_path / "state").create("write the report")
        assert (tmp_path / "state" / "t_del1.tasks.json").is_file()
        thread_ids = {t["thread_id"] for t in client.get("/api/threads").json()}
        assert "t_del1" in thread_ids

        response = client.delete("/api/threads/t_del1")

        assert response.status_code == 200
        assert response.json() == {"deleted": "t_del1"}
        assert not (tmp_path / "state" / "t_del1.tasks.json").exists()
        thread_ids = {t["thread_id"] for t in client.get("/api/threads").json()}
        assert "t_del1" not in thread_ids


def test_delete_thread_endpoint_unknown_thread_404s_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.delete("/api/threads/nope")

    assert response.status_code == 404


def test_rename_thread_endpoint_overrides_preview_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_rename1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "original first message"})
            _receive_until(ws, "tasks_changed")

        before = next(t for t in client.get("/api/threads").json() if t["thread_id"] == "t_rename1")
        assert "original first message" in before["preview"]

        response = client.post(
            "/api/threads/t_rename1/rename", json={"title": "Quarterly report draft"}
        )

        assert response.status_code == 200
        assert response.json() == {"thread_id": "t_rename1", "title": "Quarterly report draft"}
        after = next(t for t in client.get("/api/threads").json() if t["thread_id"] == "t_rename1")
        assert after["preview"] == "Quarterly report draft"


def test_rename_thread_endpoint_unknown_thread_404s_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post("/api/threads/nope/rename", json={"title": "New title"})

    assert response.status_code == 404


def test_rename_thread_endpoint_rejects_blank_title_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_rename2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        response = client.post("/api/threads/t_rename2/rename", json={"title": "   "})

    assert response.status_code == 400


def test_delete_thread_endpoint_removes_title_sidecar_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_del_title") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        client.post("/api/threads/t_del_title/rename", json={"title": "renamed"})
        assert (tmp_path / "state" / "t_del_title.title").is_file()

        response = client.delete("/api/threads/t_del_title")

        assert response.status_code == 200
        assert not (tmp_path / "state" / "t_del_title.title").exists()


def _stub_script_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_python_script normally provisions a dedicated venv on first use
    (tools/script_env.py's ensure_script_env) -- real, but slow and
    unnecessary for a test that only cares about whether the exec-policy
    check let the call through, not about the script actually needing a
    separate environment. Runs the trivial scripts these tests use with
    whatever Python is already running pytest instead."""
    monkeypatch.setattr(
        "coscribe.tools.scripts.ensure_script_env", lambda state_dir: Path(sys.executable).parent
    )
    monkeypatch.setattr("coscribe.tools.scripts.venv_python", lambda venv_dir: Path(sys.executable))


def test_exec_policy_allow_rule_skips_approval_and_runs_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_script_env(monkeypatch)
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(
        json.dumps({"rules": [{"pattern": r"print\(", "decision": "allow"}]}), encoding="utf-8"
    )
    call = _tool_call(
        "call_1", "run_python_script", {"script": "print('ok')", "description": "print ok"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, exec_policy_path=policy_path) as client:
        with client.websocket_connect("/ws/t_exec1") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "run it"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    assert "tool_result" in types

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].tool_name == "run_python_script"
    assert entries[0].decision == "approve"
    assert entries[0].reason == "exec_policy"


def test_exec_policy_forbidden_rule_rejects_without_approval_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_script_env(monkeypatch)
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "pattern": r"shutil\.rmtree",
                        "decision": "forbidden",
                        "justification": "no bulk deletes",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    call = _tool_call(
        "call_1",
        "run_python_script",
        {"script": "import shutil; shutil.rmtree('/tmp/x')", "description": "delete a dir"},
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, exec_policy_path=policy_path) as client:
        with client.websocket_connect("/ws/t_exec2") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "run it"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    tool_result = next(m for m in messages if m["type"] == "tool_result")
    assert "no bulk deletes" in json.dumps(tool_result)

    entries = AuditLog(tmp_path / "state").read_all()
    assert len(entries) == 1
    assert entries[0].tool_name == "run_python_script"
    assert entries[0].decision == "reject"
    assert entries[0].reason == "exec_policy"
    assert entries[0].detail == "no bulk deletes"


def test_exec_policy_no_match_falls_through_to_normal_approval_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_script_env(monkeypatch)
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(
        json.dumps({"rules": [{"pattern": r"this-does-not-appear", "decision": "allow"}]}),
        encoding="utf-8",
    )
    call = _tool_call(
        "call_1", "run_python_script", {"script": "print('ok')", "description": "print ok"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, exec_policy_path=policy_path) as client:
        with client.websocket_connect("/ws/t_exec3") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "run it"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

    entries = AuditLog(tmp_path / "state").read_all()
    assert entries[0].reason == "human"


def test_exec_policy_allow_does_not_override_plan_mode_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_script_env(monkeypatch)
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(
        json.dumps({"rules": [{"pattern": r"print\(", "decision": "allow"}]}), encoding="utf-8"
    )
    call = _tool_call(
        "call_1", "run_python_script", {"script": "print('ok')", "description": "print ok"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, exec_policy_path=policy_path) as client:
        with client.websocket_connect("/ws/t_exec4") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/plan"})
            ws.receive_json()  # state (plan_mode: True)

            ws.send_json({"type": "user_message", "text": "run it"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    entries = AuditLog(tmp_path / "state").read_all()
    assert entries[0].decision == "reject"
    assert entries[0].reason == "plan_mode"


def test_browse_dirs_lists_subdirectories_only_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: app.py never
    defined /api/browse-dirs at all (unlike web/app.py), even though the
    shared app.js frontend's Settings-panel folder picker calls it
    unconditionally -- clicking "select folder" 404'd and the picker
    modal opened with nothing in it, with no visible error."""
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Documents").mkdir()
    (tmp_path / "not_a_dir.txt").write_text("x", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/browse-dirs", params={"path": str(tmp_path)})

    assert response.status_code == 200
    body = response.json()
    resolved = tmp_path.resolve()
    assert body["path"] == str(resolved)
    assert body["directories"] == [
        {"name": "Documents", "path": str(resolved / "Documents")},
        {"name": "Downloads", "path": str(resolved / "Downloads")},
        # create_app_lg() auto-creates settings.skills_dir at startup --
        # the skills_by_name dict built for GET /api/skills/select_skills
        # validation calls load_skills(settings.skills_dir), which
        # creates it if missing.
        {"name": "skills", "path": str(resolved / "skills")},
        # The lifespan also opens the AsyncSqliteSaver checkpointer under
        # settings.state_dir (tmp_path/state), creating that directory too
        # -- app.py's equivalent test has no state_dir-creating lifespan
        # step, so this entry is specific to runtime_lg.
        {"name": "state", "path": str(resolved / "state")},
    ]
    assert body["parent"] == str(resolved.parent)


def test_browse_dirs_defaults_to_home_when_no_path_given_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/browse-dirs")

    assert response.status_code == 200
    assert response.json()["path"] == str(Path.home().resolve())


def test_browse_dirs_nonexistent_path_returns_error_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get(
            "/api/browse-dirs", params={"path": str(tmp_path / "does-not-exist")}
        )

    assert response.status_code == 200
    assert "error" in response.json()


def test_browse_dirs_file_path_returns_error_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    file_path = tmp_path / "a.txt"
    file_path.write_text("x", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/browse-dirs", params={"path": str(file_path)})

    assert response.status_code == 200
    assert "error" in response.json()


def test_history_is_empty_for_a_brand_new_thread_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_hist_new") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert history == {"type": "history", "entries": [], "has_older": False}


def test_history_reports_has_older_true_after_reconnecting_to_a_compacted_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real, live-reported bug: the frontend's "load earlier messages"
    control used to be offered unconditionally on *every* thread,
    including a brand-new one with nothing to page through -- this
    `has_older` flag is what the frontend now gates that control on.
    Regression-tests the O(1) proxy itself: a thread that's been
    /compact'd, then reconnected to (a fresh connection, so send_history
    -- not any state left over from the compacting connection -- is what
    has to get this right), reports has_older=True."""
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="hi there!"),
            AIMessage(content="nice to hear"),
            AIMessage(content="a short summary of the chat"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_hist_has_older") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history -- empty, brand new thread
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "how are you"})
            _receive_until(ws, "tasks_changed")
            ws.send_json({"type": "user_message", "text": "/compact"})
            assert ws.receive_json()["type"] == "compacted"

        with client.websocket_connect("/ws/t_hist_has_older") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert history["has_older"] is True


def test_reconnect_replays_conversation_history_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: the chat log was
    only ever built from *live* events during the current WS connection,
    so switching to (or reconnecting to) an existing thread always showed
    a blank pane even though the model still remembered the whole
    conversation -- see serialize_history_for_ws_lg's docstring."""
    call = _tool_call("call_1", "read_file", {"path": "note.txt"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="the file says hello"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_hist_reconnect") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history -- empty, brand new thread
            ws.send_json({"type": "user_message", "text": "what does note.txt say?"})
            _receive_until(ws, "tasks_changed")

        # Reconnect -- a fresh WS connection to the same thread, exactly
        # what switching sessions or reloading the page does.
        with client.websocket_connect("/ws/t_hist_reconnect") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert history == {
        "type": "history",
        "entries": [
            {"kind": "user", "text": "what does note.txt say?"},
            {
                "kind": "tool",
                "tool_name": "read_file",
                "arguments": {"path": "note.txt"},
                "result": "File does not exist: note.txt",
                "is_error": True,
            },
            {"kind": "agent", "text": "the file says hello"},
        ],
        "has_older": False,
    }


def test_reconnect_replays_a_sent_images_data_urls_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real, user-reported bug: an attached image rendered fine for the
    rest of the live WS connection that sent it (the browser's own
    optimistic local echo, still holding the data URL in memory), but
    vanished on reload/reconnect/thread-switch-and-back -- the image was
    already faithfully checkpointed (baked into the HumanMessage's own
    image_url content blocks), just never read back out by
    serialize_history_for_ws_lg. See messages.py's extract_images and
    this same function's docstring."""
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="nice photo")])
    data_url = "data:image/png;base64,aGVsbG8="
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_hist_images") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history -- empty, brand new thread
            ws.send_json({"type": "user_message", "text": "what's in this?", "images": [data_url]})
            _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/t_hist_images") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert history == {
        "type": "history",
        "entries": [
            {"kind": "user", "text": "what's in this?", "images": [data_url]},
            {"kind": "agent", "text": "nice photo"},
        ],
        "has_older": False,
    }


def test_history_omits_a_call_still_pending_approval_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tool call awaiting approval has no ToolMessage result yet --
    serialize_history_for_ws_lg must skip it rather than show a
    phantom step with no outcome; resume_after_reconnect already
    redelivers the live approval_required event for it separately."""
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_hist_pending") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            # Disconnect without ever answering -- the call stays pending.

        with client.websocket_connect("/ws/t_hist_pending") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert history == {
        "type": "history",
        "entries": [{"kind": "user", "text": "write hi to note.txt"}],
        "has_older": False,
    }


def test_history_replay_includes_an_approved_calls_real_result_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: reconnecting to (or
    reloading) an existing thread showed every past tool call as click-to-
    expand, but expanding one showed nothing -- ToolCallRow's own `result
    !== undefined` guard always failed on a replayed item, because
    serialize_history_for_ws_lg computed each call's real result (into
    results_by_id, to decide whether to include the entry at all) and then
    silently dropped it instead of putting it on the emitted entry. Uses
    an *approved* call specifically (not the plain read_file case the
    sibling reconnect test above already covers), since that's the
    real-world shape reported live -- an approval-gated run_command/
    write-file call whose result vanished on reload."""
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_hist_approved_result") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write hi to note.txt"})
            approval = ws.receive_json()
            assert approval["type"] == "approval_required"
            ws.send_json({"type": "approval_response", "id": approval["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/t_hist_approved_result") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    tool_entry = next(e for e in history["entries"] if e["kind"] == "tool")
    assert tool_entry["result"] == {
        "path": "note.txt",
        "bytes_written": 2,
        "lines_added": 1,
        "lines_removed": 0,
    }
    assert tool_entry["is_error"] is False


def _run_turn(client: Any, thread_id: str, text: str, accept_edits: bool = True) -> None:
    with client.websocket_connect(f"/ws/{thread_id}") as ws:
        ws.receive_json()  # state
        ws.receive_json()  # history
        if accept_edits:
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            _receive_until(ws, "state")
        ws.send_json({"type": "user_message", "text": text})
        _receive_until(ws, "tasks_changed")


def test_thread_activity_lists_outputs_references_tools_and_progress_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_script_env(monkeypatch)
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "input.txt").write_text("data", encoding="utf-8")
    script = "open('chart.png', 'w').write('png')"
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call("c1", "task_create", {"content": "Read the input"}),
                    _tool_call("c2", "read_file", {"path": "input.txt"}),
                    _tool_call("c3", "list_files", {"path": "."}),
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call("c4", "write_file", {"path": "report.md", "content": "# R"}),
                    _tool_call("c5", "write_file", {"path": "../outside.md", "content": "x"}),
                ],
            ),
            # Its own turn: a script's files_written is a before/after
            # snapshot of the workspace, so a write running alongside it
            # would be counted as the script's too.
            AIMessage(
                content="",
                tool_calls=[
                    _tool_call("c6", "run_python_script", {"script": script, "description": "d"}),
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        _run_turn(client, "t_activity", "make a report")
        activity = client.get("/api/threads/t_activity/activity").json()

    assert [t["content"] for t in activity["tasks"]] == ["Read the input"]
    # Newest first; the write outside the workspace failed, so it isn't one.
    assert [o["path"] for o in activity["outputs"]] == ["chart.png", "report.md"]
    assert all(o["exists"] and o["openable"] for o in activity["outputs"])
    assert [r["path"] for r in activity["references"]] == ["input.txt"]
    assert {t["name"]: t["count"] for t in activity["tools"]} == {
        "read_file": 1,
        "list_files": 1,
        "write_file": 1,
        "run_python_script": 1,
    }
    assert activity["connectors"] == [] and activity["skills"] == []


def test_opening_a_thread_file_only_hands_documents_to_the_os_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        "coscribe.web.app.open_in_os", lambda path, *, reveal: opened.append((path.name, reveal))
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "report.xlsx").write_bytes(b"x")
    (workspace / "run.bat").write_text("echo hi", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("s", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        url = "/api/threads/t_files/files/open"
        doc = client.post(url, json={"path": "report.xlsx"})
        script = client.post(url, json={"path": "run.bat"})
        revealed = client.post(url, json={"path": "run.bat", "reveal": True})
        outside = client.post(url, json={"path": "../secret.txt"})
        download = client.get("/api/threads/t_files/files/download", params={"path": "report.xlsx"})
        missing = client.get("/api/threads/t_files/files/download", params={"path": "nope.xlsx"})

    assert doc.status_code == 200 and script.status_code == 400
    assert revealed.status_code == 200 and outside.status_code == 404
    assert opened == [("report.xlsx", False), ("run.bat", True)]
    assert download.status_code == 200 and download.content == b"x"
    assert "report.xlsx" in download.headers["content-disposition"]
    assert missing.status_code == 404


def test_a_json_body_without_a_json_content_type_is_refused_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cross-site page can POST to 127.0.0.1 without a CORS preflight
    only if it sends no JSON Content-Type -- such a body must not be
    parsed, or any website could drive these endpoints."""
    opened: list[Any] = []
    monkeypatch.setattr("coscribe.web.app.open_in_os", lambda path, *, reveal: opened.append(path))
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "report.xlsx").write_bytes(b"x")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        request = client.build_request(
            "POST", "/api/threads/t_csrf/files/open", content=b'{"path": "report.xlsx"}'
        )
        assert "content-type" not in request.headers
        response = client.send(request)

    assert response.status_code == 422
    assert opened == []


_NOTES_WORKFLOW: dict[str, Any] = {
    "inputs": [{"name": "source", "default": "notes.txt"}],
    "steps": [
        {"id": "read", "kind": "tool", "title": "Read notes", "tool": "read_file",
         "args": {"path": "{{source}}"}, "save_as": "content"},
        {"id": "count", "kind": "llm", "title": "Count words",
         "prompt": "Count the words:\n{{content}}",
         "fields": [{"name": "words", "type": "number"}], "save_as": "tally"},
        {"id": "sane", "kind": "check", "title": "Has words",
         "conditions": [{"left": {"ref": "tally.words"}, "op": "gt", "right": {"value": 0}}]},
        {"id": "ok", "kind": "approval", "title": "Looks right?",
         "message": "{{tally.words}} words"},
        {"id": "write", "kind": "tool", "title": "Save", "tool": "write_file",
         "args": {"path": "out.md", "content": "{{tally.words}} words"}},
    ],
}  # fmt: skip


def _structured(args: dict[str, Any]) -> AIMessage:
    return AIMessage(content="", tool_calls=[_tool_call("s1", "step_result", args)])


def _create_workflow_task(client: Any) -> dict[str, Any]:
    response = client.post(
        "/api/scheduled-tasks",
        json={"name": "Word count", "kind": "manual", "at": "", "workflow": _NOTES_WORKFLOW},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_a_workflow_task_runs_waits_for_approval_and_finishes_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("one two three", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[_structured({"words": 3})])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        task = _create_workflow_task(client)
        assert task["workflow"]["steps"][0]["tool"] == "read_file"
        started = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={})
        run = started.json()["run"]
        parked = _wait_for_run_status(client, task["trigger_id"], run["run_id"])

        # Opening the run's thread like a conversation must leave it alone.
        with client.websocket_connect(f"/ws/{run['thread_id']}") as ws:
            ws.receive_json()  # state
            assert ws.receive_json()["entries"] == []

        answered = client.post(
            f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}/answer",
            json={"approved": True},
        )
        finished = _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        again = client.post(
            f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}/answer",
            json={"approved": True},
        )

    assert parked["status"] == "needs_approval"
    assert [(s["step_id"], s["status"]) for s in parked["steps"]] == [
        ("read", "done"),
        ("count", "done"),
        ("sane", "done"),
        ("ok", "waiting"),
    ]
    assert parked["steps"][1]["output"] == {"words": 3}
    assert parked["steps"][3]["output"] == "3 words"
    assert answered.status_code == 200
    assert finished["status"] == "completed" and finished["error"] is None
    assert [s["status"] for s in finished["steps"]] == ["done"] * 5
    assert (tmp_path / "workspace" / "out.md").read_text(encoding="utf-8") == "3 words"
    assert again.status_code == 409


def test_a_failed_workflow_step_can_be_retried_from_an_earlier_one_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("one two", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(
        responses=[_structured({"words": 0}), _structured({"words": 2})]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        task = _create_workflow_task(client)
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        failed = _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        client.post(
            f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}/retry",
            json={"step_id": "count"},
        )
        waiting = _wait_for_run_status(client, task["trigger_id"], run["run_id"])

    assert failed["status"] == "failed"
    assert failed["error"] == "Has words: 1 of 1 checks failed"
    assert failed["steps"][2]["checks"] == [{"held": False, "left": 0, "right": 0}]
    assert waiting["status"] == "needs_approval"
    assert [(s["step_id"], s["status"]) for s in waiting["steps"]][1:] == [
        ("count", "done"),
        ("sane", "done"),
        ("ok", "waiting"),
    ]
    assert waiting["steps"][1]["output"] == {"words": 2}


def test_a_workflow_task_with_a_bad_reference_is_refused_with_the_step_named_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = json.loads(json.dumps(_NOTES_WORKFLOW))
    broken["steps"][2]["conditions"][0]["left"] = {"ref": "taly.words"}
    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        response = client.post(
            "/api/scheduled-tasks",
            json={"name": "Broken", "kind": "manual", "at": "", "workflow": broken},
        )

    assert response.status_code == 400
    assert "step 3 ('Has words') reads 'taly.words'" in response.json()["error"]


def test_a_workflow_run_thread_is_never_driven_as_a_conversation_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("a b", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[_structured({"words": 2})])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        task = _create_workflow_task(client)
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        with client.websocket_connect(f"/ws/{run['thread_id']}") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello?"})
            error = _receive_until(ws, "error")[-1]
        # The pending approval survived the connection, untouched.
        client.post(
            f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}/answer",
            json={"approved": True},
        )
        finished = _wait_for_run_status(client, task["trigger_id"], run["run_id"])

    assert error["message"] == "A workflow run doesn't take messages."
    assert fake_model.i == 1  # only the workflow's own model step
    assert finished["status"] == "completed"


def test_a_workflow_runs_activity_comes_from_its_steps_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("a b", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[_structured({"words": 2})])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        task = _create_workflow_task(client)
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        client.post(
            f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}/answer",
            json={"approved": True},
        )
        _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        activity = client.get(f"/api/threads/{run['thread_id']}/activity").json()

    assert [o["path"] for o in activity["outputs"]] == ["out.md"]
    assert {t["name"]: t["count"] for t in activity["tools"]} == {"read_file": 1, "write_file": 1}
    assert activity["tasks"] == []


def test_workflow_draft_distills_the_threads_tool_calls_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "input.txt").write_text("data", encoding="utf-8")
    draft = {
        "name": "Read the input",
        "workflow": {
            "inputs": [{"name": "path", "default": "input.txt"}],
            "steps": [
                {
                    "id": "read",
                    "kind": "tool",
                    "title": "Read it",
                    "tool": "read_file",
                    "args": {"path": "{{path}}"},
                    "save_as": "text",
                }
            ],
        },
        "notes": ["The path became an input."],
    }
    read = _tool_call("c1", "read_file", {"path": "input.txt"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[read]),
            AIMessage(content="done"),
            AIMessage(content=json.dumps(draft)),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        empty = client.post("/api/threads/t_empty/workflow-draft", json={})
        _run_turn(client, "t_draft", "read input.txt")
        response = client.post("/api/threads/t_draft/workflow-draft", json={"name": ""})

    assert empty.status_code == 400
    assert "hasn't used any tools" in empty.json()["error"]
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Read the input"
    assert body["notes"] == ["The path became an input."]
    assert body["workflow"]["steps"][0]["args"] == {"path": "{{path}}"}
    curator_request = str(fake_model.received[-1][-1].content)
    assert '[tool call #1] read_file({"path": "input.txt"})' in curator_request
    # Nothing is saved until the person reviews the draft.
    assert ScheduledTriggerStore(tmp_path / "state").list_all() == []


def test_validate_workflow_normalizes_or_explains_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="unused")])
    step = {"id": "a", "kind": "check", "title": "A", "conditions": []}
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        ok = client.post("/api/workflows/validate", json={"workflow": {"steps": []}})
        bad = client.post("/api/workflows/validate", json={"workflow": {"steps": [step]}})

    assert ok.json() == {"workflow": {"version": 1, "inputs": [], "steps": []}}
    assert bad.status_code == 400
    assert "step 1" in bad.json()["error"]


def _loop_workflow(skip: str) -> dict[str, Any]:
    return {
        "steps": [
            {"id": "ls", "kind": "tool", "title": "List", "tool": "list_files",
             "args": {"pattern": "*.txt"}, "save_as": "listing"},
            {"id": "each", "kind": "loop", "title": "Each file", "over": "listing.files",
             "item": "file", "collect": "text", "save_as": "texts",
             "steps": [
                 {"id": "read", "kind": "tool", "title": "Read", "tool": "read_file",
                  "args": {"path": "{{file}}"}, "save_as": "text"},
                 {"id": "not_b", "kind": "check", "title": "Not B",
                  "conditions": [{"left": {"ref": "file"}, "op": "ne", "right": {"value": skip}}]},
             ]},
            {"id": "keep", "kind": "tool", "title": "Keep", "tool": "write_file",
             "args": {"path": "out/all.md", "content": "All: {{texts}}", "overwrite": True}},
        ]
    }  # fmt: skip


def test_a_failed_pass_is_retried_where_it_stopped_not_the_whole_loop_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("a", "b", "c"):
        (workspace / f"{name}.txt").write_text(f"text {name}", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="unused")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "Each", "kind": "manual", "at": "", "workflow": _loop_workflow("b.txt")},
        ).json()
        trigger_id = created["trigger_id"]
        run = client.post(f"/api/scheduled-tasks/{trigger_id}/run", json={}).json()["run"]
        failed = _wait_for_run_status(client, trigger_id, run["run_id"])

        fixed = {"name": "Each", "kind": "manual", "at": "", "workflow": _loop_workflow("z.txt")}
        assert client.put(f"/api/scheduled-tasks/{trigger_id}", json=fixed).status_code == 200
        client.post(f"/api/scheduled-tasks/{trigger_id}/runs/{run['run_id']}/retry", json={})
        done = _wait_for_run_status(client, trigger_id, run["run_id"])
        activity = client.get(f"/api/threads/{run['thread_id']}/activity").json()

    assert failed["status"] == "failed"
    assert failed["error"] == "Not B: 1 of 1 checks failed"
    stopped = {(s["step_id"], tuple(s["iteration"])): s["status"] for s in failed["steps"]}
    assert stopped[("not_b", (1,))] == "failed" and stopped[("each", ())] == "failed"
    assert done["status"] == "completed", done["error"]
    reads = [s for s in done["steps"] if s["step_id"] == "read"]
    assert [s["iteration"] for s in reads] == [[0], [1], [2]]
    # The first pass wasn't run again.
    first_read = next(s for s in failed["steps"] if s["step_id"] == "read")
    assert reads[0]["finished_at"] == first_read["finished_at"]
    assert (workspace / "out" / "all.md").read_text(encoding="utf-8").count("text") == 3
    assert {t["name"]: t["count"] for t in activity["tools"]} == {
        "list_files": 1, "read_file": 3, "write_file": 1,
    }  # fmt: skip
    assert [o["path"] for o in activity["outputs"]] == ["out/all.md"]


def test_the_chat_model_drafts_a_fixed_workflow_without_saving_it_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "input.txt").write_text("data", encoding="utf-8")
    draft = {
        "name": "Read the input",
        "workflow": {
            "steps": [{"id": "read", "kind": "tool", "title": "Read it", "tool": "read_file",
                       "args": {"path": "input.txt"}, "save_as": "text"}],
        },
        "notes": [],
    }  # fmt: skip
    read = _tool_call("c1", "read_file", {"path": "input.txt"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[read]),
            AIMessage(content="", tool_calls=[_tool_call("c2", "draft_workflow", {"name": ""})]),
            AIMessage(content=json.dumps(draft)),
            AIMessage(content="Drafted -- review it on the card."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_chat_draft") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            _receive_until(ws, "state")
            ws.send_json({"type": "user_message", "text": "read input.txt, then make it fixed"})
            streamed = _receive_until(ws, "tasks_changed")
        with client.websocket_connect("/ws/t_chat_draft") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    [result] = [e for e in history["entries"] if e.get("tool_name") == "draft_workflow"]
    assert result["result"]["status"] == "drafted", result["result"]
    assert result["result"]["workflow"]["steps"][0]["tool"] == "read_file"
    assert result["result"]["workspace"] is None
    # The curator saw the read that worked, not the draft call itself.
    curator_request = str(fake_model.received[2][-1].content)
    assert '[tool call #1] read_file({"path": "input.txt"})' in curator_request
    assert "draft_workflow(" not in curator_request
    # The curator's own reply isn't the assistant talking.
    chat_text = "".join(
        str(m.get("text", "")) for m in streamed if m["type"] in ("agent_delta", "agent_message")
    )
    assert '"workflow"' not in chat_text
    assert "Drafted -- review it on the card." in chat_text
    assert ScheduledTriggerStore(tmp_path / "state").list_all() == []


def test_a_tasks_runs_work_in_its_own_workspace_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "only-here.txt").write_text("found me", encoding="utf-8")
    workflow = {
        "steps": [{"id": "read", "kind": "tool", "title": "Read", "tool": "read_file",
                   "args": {"path": "only-here.txt"}, "save_as": "text"}],
    }  # fmt: skip
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="unused")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        missing = client.post(
            "/api/scheduled-tasks",
            json={"name": "R", "kind": "manual", "at": "", "workflow": workflow,
                  "workspace": str(tmp_path / "nowhere")},
        )  # fmt: skip
        task = client.post(
            "/api/scheduled-tasks",
            json={"name": "R", "kind": "manual", "at": "", "workflow": workflow,
                  "workspace": str(folder)},
        ).json()  # fmt: skip
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        done = _wait_for_run_status(client, task["trigger_id"], run["run_id"])

    assert missing.status_code == 400 and "isn't an existing folder" in missing.json()["error"]
    assert task["workspace"] == str(folder)
    assert done["status"] == "completed", done.get("error")
    assert done["steps"][0]["output"] == "found me"


def test_a_new_default_model_applies_to_the_next_session_without_a_restart_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hi")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/config",
            json={"updates": {"COSCRIBE_DEFAULT_MODEL": "deepseek:deepseek-pro",
                              "COSCRIBE_MAX_TURNS": "7"}},
        )  # fmt: skip
        with client.websocket_connect("/ws/t_after_default") as ws:
            state = ws.receive_json()

    assert response.json()["restart_required"] is False
    assert state["model"] == "deepseek:deepseek-pro"
    assert dotenv_values(tmp_path / ".env")["COSCRIBE_MAX_TURNS"] == "7"


def test_a_conversation_is_named_after_its_first_exchange_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="Here's the summary."), AIMessage(content='"Q3 销售汇总"')]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, auto_title_threads=True) as client:
        with client.websocket_connect("/ws/t_named") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "帮我汇总一下第三季度的销售数据"})
            _receive_until(ws, "tasks_changed")
            titled = _receive_until(ws, "thread_titled")[-1]
        threads = client.get("/api/threads").json()

    assert titled == {"type": "thread_titled", "title": "Q3 销售汇总"}
    [thread] = [t for t in threads if t["thread_id"] == "t_named"]
    assert thread["preview"] == "Q3 销售汇总"
    title_request = str(fake_model.received[-1][-1].content)
    assert title_request.startswith("User: 帮我汇总一下第三季度的销售数据")


def test_a_tool_call_is_announced_before_it_runs_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "list_files", {"path": "."})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_tool_started") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "list files"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    started = messages[types.index("tool_started")]
    assert started["tool_name"] == "list_files"
    assert started["arguments"] == {"path": "."}
    assert types.index("tool_started") < types.index("tool_result")


def test_a_call_waiting_for_approval_is_shown_only_as_its_approval_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_tool_started_gated") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "write it"})
            before = _receive_until(ws, "approval_required")
            ws.send_json({"type": "approval_response", "id": before[-1]["id"], "approved": True})
            after = _receive_until(ws, "tasks_changed")

    assert not any(m["type"] == "tool_started" for m in before + after)


def test_an_auto_approved_call_is_announced_before_it_runs_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    call = _tool_call("call_1", "write_file", {"path": "note.txt", "content": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_tool_started_auto") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            ws.receive_json()  # state
            ws.send_json({"type": "user_message", "text": "write it"})
            messages = _receive_until(ws, "tasks_changed")

    types = [m["type"] for m in messages]
    assert "approval_required" not in types
    assert types.index("tool_started") < types.index("tool_result")
    assert messages[types.index("tool_started")]["tool_name"] == "write_file"


def _delegating_model(child_reply: str = "child done") -> FakeToolCallingChatModel:
    spawn = _tool_call(
        "call_spawn",
        "spawn_agent",
        {"description": "write the note", "prompt": "write note.txt", "tool_names": "write_file"},
    )
    write = _tool_call("call_write", "write_file", {"path": "note.txt", "content": "hi"})
    return FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[spawn]),
            AIMessage(content="", tool_calls=[write]),
            AIMessage(content=child_reply),
            AIMessage(content="parent done"),
        ]
    )


@pytest.mark.parametrize(
    ("mode", "written"), [("/accept-edits", True), ("/plan", False)], ids=["accept-edits", "plan"]
)
def test_a_sub_agent_follows_the_conversations_approval_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str, written: bool
) -> None:
    with _client_lg(tmp_path, monkeypatch, _delegating_model()) as client:
        with client.websocket_connect("/ws/t_sub_mode") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": mode})
            ws.receive_json()  # state
            ws.send_json({"type": "user_message", "text": "delegate the note"})
            messages = _receive_until(ws, "tasks_changed")

    assert not any(m["type"] == "approval_required" for m in messages)
    assert (tmp_path / "workspace" / "note.txt").exists() is written
    [task] = SubAgentTaskStore(tmp_path / "state").list_for_thread("t_sub_mode")
    assert task.status == "succeeded"


def test_finished_sub_agents_can_be_cleared_and_a_finished_one_cant_be_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client_lg(tmp_path, monkeypatch, _delegating_model()) as client:
        with client.websocket_connect("/ws/t_sub_clear") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            ws.receive_json()  # state
            ws.send_json({"type": "user_message", "text": "delegate the note"})
            _receive_until(ws, "tasks_changed")

        [task] = client.get("/api/threads/t_sub_clear/subagents").json()
        transcript = client.get(f"/api/subagents/{task['task_id']}/transcript").json()
        stop = client.post(f"/api/subagents/{task['task_id']}/stop")
        cleared = client.delete("/api/threads/t_sub_clear/subagents").json()
        after = client.get("/api/threads/t_sub_clear/subagents").json()

    assert task["model"] == "fake:model"
    assert [e["kind"] for e in transcript["entries"]] == ["user", "tool", "agent"]
    assert stop.status_code == 409
    assert cleared == {"removed": 1}
    assert after == []


class _BackgroundDelegationModel(ConcurrentSpawnFakeModel):
    """The parent hands off a background run and ends its turn; the child
    then writes a file. Routed by content, since both use one model."""

    def _respond(self, messages: list[BaseMessage]) -> AIMessage:
        text = " ".join(str(getattr(m, "content", "")) for m in messages)
        has_tool_result = any(isinstance(m, ToolMessage) for m in messages)
        if "BG marker" in text:
            if has_tool_result:
                return AIMessage(content="child done")
            call = _tool_call("call_w", "write_file", {"path": "bg.txt", "content": "BG"})
            return AIMessage(content="", tool_calls=[call])
        if has_tool_result:
            return AIMessage(content="it's running")
        spawn = _tool_call(
            "call_bg",
            "spawn_agent_background",
            {"description": "write bg.txt", "prompt": "write bg.txt -- BG marker"},
        )
        return AIMessage(content="", tool_calls=[spawn])


def test_a_background_sub_agent_waits_for_approval_after_the_tab_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SubAgentTaskStore(tmp_path / "state")

    def wait_for(status: str) -> Any:
        for _ in range(500):
            tasks = store.list_for_thread("t_sub_bg")
            if tasks and tasks[0].status == status:
                return tasks[0]
            time.sleep(0.01)
        raise AssertionError(f"never reached {status}: {tasks}")

    with _client_lg(tmp_path, monkeypatch, _BackgroundDelegationModel()) as client:
        with client.websocket_connect("/ws/t_sub_bg") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "delegate it in the background"})
            _receive_until(ws, "tasks_changed")

        waiting = wait_for("needs_approval")
        with client.websocket_connect("/ws/t_sub_bg") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            approval_id = waiting.pending_approval["id"]
            ws.send_json({"type": "approval_response", "id": approval_id, "approved": True})
            done = wait_for("succeeded")

    assert done.result == "child done"
    assert (tmp_path / "workspace" / "bg.txt").read_text() == "BG"


async def test_a_sub_agents_approval_survives_a_closed_turn_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from coscribe.tools import subagent_tasks
    from coscribe.tools.subagent_tasks import SubAgentTask
    from coscribe.web.session import _SubAgentApprovalChannel

    monkeypatch.setitem(subagent_tasks._RUNNING, "t1", asyncio.get_running_loop().create_future())

    class _ClosedSocket:
        async def send_json(self, payload: dict[str, Any]) -> None:
            raise RuntimeError('Cannot call "send" once a close message has been sent.')

    changed: list[str] = []

    async def subagent_changed(task: SubAgentTask) -> None:
        changed.append(task.status)

    session = SimpleNamespace(
        _live_websocket=None,
        _turn_websocket=_ClosedSocket(),
        settings=SimpleNamespace(state_dir=tmp_path),
        _subagent_changed=subagent_changed,
    )
    task = SubAgentTask(
        task_id="t1",
        thread_id="thread",
        instructions="",
        prompt="p",
        tool_names="",
        description="d",
        status="running",
        started_at="2026-09-26T00:00:00+00:00",
    )
    channel = _SubAgentApprovalChannel(session, task)  # type: ignore[arg-type]

    await channel.send_json({"type": "approval_required", "id": "r1", "tool_name": "write_file"})

    saved = SubAgentTaskStore(tmp_path).load("t1")
    assert saved is not None and saved.status == "needs_approval"
    assert saved.pending_approval == {"id": "r1", "tool_name": "write_file"}
    assert changed == ["needs_approval"]


def _saved_workflow_task(tmp_path: Path) -> Any:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore, create_trigger

    workflow = {
        "steps": [{"id": "read", "kind": "tool", "title": "Read it", "tool": "read_file",
                   "args": {"path": "input.txt"}, "save_as": "text"}],
    }  # fmt: skip
    return create_trigger(
        ScheduledTriggerStore(tmp_path / "state"),
        name="Read the input",
        kind="manual",
        at="",
        prompt="",
        workflow=workflow,
    )


def test_revising_a_saved_workflow_proposes_changes_without_saving_them_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    task = _saved_workflow_task(tmp_path)
    revised = {
        "workflow": {
            "steps": [
                {"id": "read", "kind": "tool", "title": "Read it", "tool": "read_file",
                 "args": {"path": "input.txt"}, "save_as": "text"},
                {"id": "check", "kind": "check", "title": "Not empty",
                 "conditions": [{"left": {"ref": "text"}, "op": "not_empty"}]},
            ],
        },
        "changes": ["Stops the run when the file is empty"],
        "notes": [],
    }  # fmt: skip
    revise = _tool_call(
        "c1", "revise_workflow", {"task_id": task.trigger_id, "request": "stop if it's empty"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[revise]),
            AIMessage(content=json.dumps(revised)),
            AIMessage(content="Review the changes on the card."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_revise") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "make it stop if the file is empty"})
            messages = _receive_until(ws, "tasks_changed")

    [result] = [m for m in messages if m.get("tool_name") == "revise_workflow" and "result" in m]
    raw = result["result"]
    proposal = json.loads(raw) if isinstance(raw, str) else raw
    assert proposal["status"] == "drafted", proposal
    assert proposal["trigger_id"] == task.trigger_id
    assert proposal["changes"] == ["Stops the run when the file is empty"]
    assert [s["id"] for s in proposal["workflow"]["steps"]] == ["read", "check"]
    reviser_request = str(fake_model.received[1][-1].content)
    assert "stop if it's empty" in reviser_request
    assert '"id": "read"' in reviser_request
    saved = ScheduledTriggerStore(tmp_path / "state").load(task.trigger_id)
    assert saved is not None and len(saved.workflow["steps"]) == 1


def test_editing_a_saved_task_asks_the_user_with_the_whole_task_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore, create_trigger

    store = ScheduledTriggerStore(tmp_path / "state")
    task = create_trigger(
        store, name="Morning digest", kind="daily", at="09:00", prompt="Summarize the news."
    )
    edit = _tool_call(
        "c1", "edit_scheduled_task", {"trigger_id": task.trigger_id, "at": "08:00"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[edit]), AIMessage(content="Left it as is.")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_edit_task") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "run it at 8 instead"})
            draft_event = _receive_until(ws, "task_draft_required")[-1]
            ws.send_json(
                {
                    "type": "question_response",
                    "id": draft_event["id"],
                    "answer": "The user dismissed the draft without saving it.",
                }
            )
            _receive_until(ws, "tasks_changed")

    draft = draft_event["draft"]
    assert draft["trigger_id"] == task.trigger_id
    assert draft["changed"] == ["at"]
    assert (draft["name"], draft["kind"], draft["at"]) == ("Morning digest", "daily", "08:00")
    assert draft["prompt"] == "Summarize the news."
    unchanged = store.load(task.trigger_id)
    assert unchanged is not None and unchanged.schedule.at == "09:00"

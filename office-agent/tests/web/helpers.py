# ruff: noqa: E402
"""Fakes and builders shared by the web tests."""

import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from typing import Any

import keyring.errors
import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from fastapi.testclient import TestClient
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from coscribe.config import Settings
from coscribe.runtime import secrets as secrets_module
from coscribe.web.app import create_app_lg


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
        "coscribe.conversation.session.resolve_chat_model",
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
        name: str, config: Any, *args: Any, **kwargs: Any
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


def _run_turn(client: Any, thread_id: str, text: str, accept_edits: bool = True) -> None:
    with client.websocket_connect(f"/ws/{thread_id}") as ws:
        ws.receive_json()  # state
        ws.receive_json()  # history
        if accept_edits:
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            _receive_until(ws, "state")
        ws.send_json({"type": "user_message", "text": text})
        while True:
            message = ws.receive_json()
            if message["type"] == "tasks_changed":
                return
            # Accept Edits still asks before running code; approve those too.
            if accept_edits and message["type"] == "approval_required":
                ws.send_json({"type": "approval_response", "id": message["id"], "approved": True})


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


def _write_call(call_id: str, path: str) -> Any:
    return _tool_call(call_id, "write_file", {"path": path, "content": "hi"})


def _verdict(decision: str, reason: str) -> AIMessage:
    return AIMessage(content=json.dumps({"decision": decision, "reason": reason}))


def _start_mode(ws: Any, mode: str) -> dict[str, Any]:
    ws.receive_json()  # state
    ws.receive_json()  # history
    ws.send_json({"type": "user_message", "text": mode})
    return ws.receive_json()

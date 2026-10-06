"""Code conversations: each message is a Codex turn
(tests/fake_codex_app_server.py), shown, approved and kept like a chat
turn."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from langgraph.checkpoint.memory import InMemorySaver

import coscribe.cli  # noqa: F401 -- web.session imports cli first
from coscribe.code_runtime.launch import CodexHost, LaunchSpec
from coscribe.config import Settings
from coscribe.runtime import empty_hooks_config
from coscribe.web.code_session import PLAN_NOTE, CodeSession, codex_thread_path

FAKE = Path(__file__).with_name("fake_codex_app_server.py")


class _Socket:
    def __init__(self, answer: Callable[[dict[str, Any]], bool] | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.answer = answer
        self.session: CodeSession | None = None

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
        if payload.get("type") == "approval_required" and self.answer and self.session:
            self.session.resolve_approval(payload["id"], self.answer(payload))

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [p for p in self.sent if p.get("type") == kind]


class _Service:
    def __init__(self, host: CodexHost) -> None:
        self.host = host

    def custom_providers(self) -> dict[str, dict[str, str]]:
        return {"fake": {"base_url": "http://127.0.0.1:9/v1", "api_key": "k"}}

    async def prepare(self) -> CodexHost:
        return self.host


class _ContextWindow:
    def get_context_window(self, model: str) -> int:
        return 128000


def _host(tmp_path: Path) -> CodexHost:
    def spec() -> LaunchSpec:
        home = tmp_path / "codex-home"
        return LaunchSpec(
            argv=(sys.executable, str(FAKE)),
            env={
                **os.environ,
                "FAKE_CODEX_LOG": str(tmp_path / "requests.jsonl"),
                "CODEX_HOME": str(home),
            },
            config="",
            home=home,
            log_path=tmp_path / "app-server.log",
        )

    return CodexHost(spec)


@pytest.fixture
async def codex(tmp_path: Path) -> Any:
    host = _host(tmp_path)
    yield host
    await host.shutdown(force=True)


class _Model(BaseChatModel):
    """The chat model the session builds around the turn; never called."""

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
        return self

    def _generate(
        self, messages: list[BaseMessage], stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> ChatResult:
        raise AssertionError("a code conversation never calls the chat model")

    @property
    def _llm_type(self) -> str:
        return "unused"


def _session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    codex: CodexHost,
    *,
    checkpointer: Any = None,
    code_model: str | None = "fake:model",
    mode: str = "manual",
) -> CodeSession:
    monkeypatch.setattr(
        "coscribe.web.session.resolve_chat_model", lambda name, custom_providers=None: _Model()
    )
    monkeypatch.setattr("coscribe.web.code_session.code_service", lambda settings: _Service(codex))
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        default_model="anthropic:claude",
        code_model=code_model,
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        skills_dir=tmp_path / "skills",
        memory_path=tmp_path / "MEMORY.md",
        auto_title_threads=False,
        default_permission_mode=mode,
    )
    folder = tmp_path / "workspace"
    folder.mkdir(parents=True, exist_ok=True)
    return CodeSession(
        thread_id="code-t1",
        settings=settings,
        context_window_client=_ContextWindow(),
        custom_providers={},
        extra_tools=[],
        checkpointer=checkpointer or InMemorySaver(),
        hooks_config=empty_hooks_config(),
        enabled_skill_names=set(),
        workspace_root=folder,
    )


async def _say(session: CodeSession, socket: _Socket, text: str) -> None:
    socket.session = session
    await session.handle_user_message(text, socket)  # type: ignore[arg-type]


async def _history(session: CodeSession) -> list[dict[str, Any]]:
    socket = _Socket()
    await session.send_history(socket)  # type: ignore[arg-type]
    [history] = socket.of("history")
    return list(history["entries"])


def _requests(tmp_path: Path) -> list[dict[str, Any]]:
    log = tmp_path / "requests.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


async def test_a_message_is_a_codex_turn_streamed_and_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    socket = _Socket()

    await _say(session, socket, "basic")

    assert "".join(p["text"] for p in socket.of("agent_delta")) == "Hello from Codex"
    [reply] = socket.of("agent_message")
    assert reply["text"] == "Hello from Codex"
    assert socket.of("usage")[-1]["cache_read_tokens"] == 800
    entries = await _history(session)
    assert [(e["kind"], e["text"]) for e in entries] == [
        ("user", "basic"),
        ("agent", "Hello from Codex"),
    ]
    started = next(r for r in _requests(tmp_path) if r.get("method") == "thread/start")
    assert started["params"]["model"] == "model"
    assert "code module" in started["params"]["developerInstructions"]


async def test_the_conversation_carries_on_in_one_codex_thread_across_restarts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    checkpointer = InMemorySaver()
    session = _session(tmp_path, monkeypatch, codex, checkpointer=checkpointer)
    await _say(session, _Socket(), "count")
    second = _Socket()
    await _say(session, second, "count")
    await codex.shutdown(force=True)

    restarted = _host(tmp_path)
    later = _session(tmp_path, monkeypatch, restarted, checkpointer=checkpointer)
    third = _Socket()
    try:
        await _say(later, third, "count")
    finally:
        await restarted.shutdown(force=True)

    assert second.of("agent_message")[0]["text"] == "turn 2"
    assert third.of("agent_message")[0]["text"] == "turn 3"
    methods = [r.get("method") for r in _requests(tmp_path)]
    assert methods.count("thread/start") == 1
    assert codex_thread_path(tmp_path / "state", "code-t1").read_text() in json.dumps(
        _requests(tmp_path)
    )


async def test_a_thread_codex_cant_resume_starts_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    path = codex_thread_path(tmp_path / "state", "code-t1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("thr-gone", encoding="utf-8")
    socket = _Socket()

    await _say(session, socket, "count")

    assert socket.of("agent_message")[0]["text"] == "turn 1"
    assert path.read_text() != "thr-gone"


async def test_an_approved_command_runs_and_replays_from_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    socket = _Socket(answer=lambda payload: True)

    await _say(session, socket, "approve")

    [card] = socket.of("approval_required")
    assert card["tool_name"] == "run_code_command"
    assert card["arguments"]["script"] == "echo made > made.txt"
    assert (tmp_path / "workspace" / "made.txt").exists()
    [result] = socket.of("tool_result")
    assert (result["tool_name"], result["is_error"]) == ("run_code_command", False)
    entries = await _history(session)
    assert [e["kind"] for e in entries] == ["user", "tool", "agent"]
    assert entries[1]["arguments"]["script"] == "echo made > made.txt"


async def test_a_declined_command_doesnt_run_or_show_as_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    socket = _Socket(answer=lambda payload: False)

    await _say(session, socket, "approve")

    assert not (tmp_path / "workspace" / "made.txt").exists()
    assert socket.of("tool_result") == []
    assert socket.of("agent_message")[0]["text"] == "was declined"


async def test_a_plain_read_runs_without_a_card_and_shows_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    socket = _Socket(answer=lambda payload: False)

    await _say(session, socket, "read")

    assert socket.of("approval_required") == []
    kinds = [p["type"] for p in socket.sent if p["type"] in ("tool_started", "tool_result")]
    assert kinds == ["tool_started", "tool_result"]


async def test_a_file_change_card_names_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    socket = _Socket(answer=lambda payload: True)

    await _say(session, socket, "file")

    [card] = socket.of("approval_required")
    assert (card["tool_name"], card["arguments"]["paths"]) == ("apply_code_change", ["notes.md"])
    assert (tmp_path / "workspace" / "notes.md").exists()


async def test_accept_edits_lets_file_changes_through_but_asks_for_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, mode="accept-edits")
    socket = _Socket(answer=lambda payload: False)

    await _say(session, socket, "file")
    await _say(session, socket, "approve")

    assert [c["tool_name"] for c in socket.of("approval_required")] == ["run_code_command"]
    assert (tmp_path / "workspace" / "notes.md").exists()


async def test_plan_mode_tells_codex_and_declines_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, mode="plan")
    socket = _Socket(answer=lambda payload: True)

    await _say(session, socket, "approve")

    assert socket.of("approval_required") == []
    assert not (tmp_path / "workspace" / "made.txt").exists()
    [turn] = [r for r in _requests(tmp_path) if r.get("method") == "turn/start"]
    assert turn["params"]["input"][0]["text"] == PLAN_NOTE + "approve"
    assert (await _history(session))[0]["text"] == "approve"


async def test_stop_interrupts_codex_and_keeps_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    socket = _Socket()
    turn = asyncio.create_task(_say(session, socket, "hang"))
    for _ in range(100):
        if any(r.get("method") == "turn/start" for r in _requests(tmp_path)):
            break
        await asyncio.sleep(0.05)

    session.request_stop()
    await asyncio.wait_for(turn, 20)

    assert "turn/interrupt" in [r.get("method") for r in _requests(tmp_path)]
    assert socket.of("agent_message")[-1]["text"] == "[stopped]"
    assert [e["kind"] for e in await _history(session)] == ["user", "agent"]


async def test_stop_while_a_card_waits_ends_the_turn_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    socket = _Socket()
    turn = asyncio.create_task(_say(session, socket, "approve"))
    for _ in range(100):
        if session.asking:
            break
        await asyncio.sleep(0.05)
    assert session.asking

    session.request_stop()
    await asyncio.wait_for(turn, 20)

    assert not session.asking
    assert not (tmp_path / "workspace" / "made.txt").exists()
    assert socket.of("agent_message")[-1]["text"].endswith("[stopped]")


async def test_a_failed_turn_reports_its_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    socket = _Socket()

    await _say(session, socket, "fail")

    assert socket.of("error")[-1]["message"] == "model unavailable"
    assert (await _history(session))[-1]["text"] == "[failed: model unavailable]"


async def test_a_model_codex_cant_use_gets_a_reason_and_no_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, code_model=None)
    socket = _Socket()

    await _say(session, socket, "basic")

    assert "Responses API" in socket.of("error")[-1]["message"]
    assert _requests(tmp_path) == []


async def test_commands_edits_and_rewinds_are_refused_but_modes_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    socket = _Socket()

    await _say(session, socket, "/compact")
    await session.handle_edit_message(0, "x", socket)  # type: ignore[arg-type]
    await session.handle_rewind_message(0, socket)  # type: ignore[arg-type]
    await _say(session, socket, "/plan")

    assert len(socket.of("error")) == 3
    assert socket.of("state")[-1]["plan_mode"] is True
    assert _requests(tmp_path) == []


async def test_pasted_images_go_to_codex_and_stay_in_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex)
    image = "data:image/png;base64,iVBORw0KGgo="
    socket = _Socket()
    socket.session = session

    await session.handle_user_message("basic", socket, images=[image])  # type: ignore[arg-type]

    [turn] = [r for r in _requests(tmp_path) if r.get("method") == "turn/start"]
    assert turn["params"]["input"][1] == {"type": "image", "url": image}
    assert (await _history(session))[0]["images"] == [image]

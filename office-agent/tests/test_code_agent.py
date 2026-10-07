"""run_code_task in a real conversation: the chat hands a task to the code
module, Codex (tests/fake_codex_app_server.py) asks for approvals, and they
go through the conversation's own approval path."""

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
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.memory import InMemorySaver

import coscribe.cli  # noqa: F401 -- web.session imports cli first
from coscribe.code_runtime.launch import CodexHost, LaunchSpec
from coscribe.config import Settings
from coscribe.runtime import empty_hooks_config
from coscribe.runtime_lg.code_agent import CODE_APPROVAL_RISKS, CODE_TASK_TOOL
from coscribe.tools import subagent_tasks
from coscribe.tools.subagent_tasks import (
    SubAgentTaskStore,
    get_subagent_transcript,
    stop_subagent_task,
)
from coscribe.web.session import ChatSessionLG

FAKE = Path(__file__).with_name("fake_codex_app_server.py")


class _Model(BaseChatModel):
    responses: list[AIMessage]
    i: int = 0

    def bind_tools(self, tools: Any, *, tool_choice: str | None = None, **kwargs: Any) -> Any:
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
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content or "", tool_calls=message.tool_calls, id=message.id
            )
        )

    @property
    def _llm_type(self) -> str:
        return "fake-code-agent-model"


def _replies(task: str, continue_task: str = "") -> list[AIMessage]:
    # A fresh message each time: one object reused keeps one id and
    # replaces itself in the history.
    args = {"description": "Make a file", "task": task}
    if continue_task:
        args["continue_task"] = continue_task
    call = {"name": CODE_TASK_TOOL, "args": args, "id": "call_code"}
    return [AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]


class _Socket:
    def __init__(
        self, answer: Callable[[dict[str, Any]], bool] | None = None, scope: str | None = None
    ) -> None:
        self.sent: list[dict[str, Any]] = []
        self.answer = answer
        self.scope = scope
        self.session: ChatSessionLG | None = None

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)
        if payload.get("type") == "approval_required" and self.answer and self.session:
            self.session.resolve_approval(payload["id"], self.answer(payload), self.scope)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [p for p in self.sent if p.get("type") == kind]


class _Service:
    def __init__(self, host: CodexHost) -> None:
        self.host = host

    def custom_providers(self) -> dict[str, dict[str, str]]:
        return {"fake": {"base_url": "http://127.0.0.1:9/v1", "api_key": "k"}}

    async def prepare(self) -> CodexHost:
        return self.host


@pytest.fixture
async def codex(tmp_path: Path) -> Any:
    def spec() -> LaunchSpec:
        return LaunchSpec(
            argv=(sys.executable, str(FAKE)),
            env={**os.environ, "FAKE_CODEX_LOG": str(tmp_path / "requests.jsonl")},
            config="",
            home=tmp_path / "codex-home",
            log_path=tmp_path / "app-server.log",
        )

    host = CodexHost(spec)
    yield host
    await host.shutdown(force=True)


def _session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    codex: CodexHost,
    model: _Model,
    *,
    enabled: bool = True,
    default_model: str = "fake:model",
    code_model: str | None = None,
) -> ChatSessionLG:
    monkeypatch.setattr(
        "coscribe.web.session.resolve_chat_model", lambda name, custom_providers=None: model
    )
    monkeypatch.setattr("coscribe.web.session.code_service", lambda settings: _Service(codex))
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None,
        default_model=default_model,
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        skills_dir=tmp_path / "skills",
        memory_path=tmp_path / "MEMORY.md",
        auto_title_threads=False,
        default_permission_mode="manual",
        code_module_enabled=enabled,
        code_model=code_model,
    )
    folder = tmp_path / "workspace"
    folder.mkdir(parents=True, exist_ok=True)
    return ChatSessionLG(
        thread_id="t1",
        settings=settings,
        context_window_client=_ContextWindow(),
        custom_providers={},
        extra_tools=[],
        checkpointer=InMemorySaver(),
        hooks_config=empty_hooks_config(),
        enabled_skill_names=set(),
        workspace_root=folder,
    )


class _ContextWindow:
    def get_context_window(self, model: str) -> int:
        return 128000


async def _run(session: ChatSessionLG, socket: _Socket, text: str = "go") -> None:
    socket.session = session
    await session.handle_user_message(text, socket)  # type: ignore[arg-type]


def _code_result(socket: _Socket) -> Any:
    [result] = [r for r in socket.of("tool_result") if r["tool_name"] == CODE_TASK_TOOL]
    return result["result"]


def test_the_tool_and_its_gated_actions_come_with_the_setting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    on = _session(tmp_path, monkeypatch, codex, _Model(responses=[]))
    off = _session(tmp_path / "off", monkeypatch, codex, _Model(responses=[]), enabled=False)

    for name, risk in CODE_APPROVAL_RISKS.items():
        assert on._gated_tool_risks[name] == risk
        assert name not in off._gated_tool_risks


async def test_an_approved_command_is_asked_on_the_card_and_then_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("approve")))
    socket = _Socket(answer=lambda payload: True)

    await _run(session, socket)

    [card] = socket.of("approval_required")
    assert card["tool_name"] == "run_code_command"
    assert card["arguments"]["script"] == "echo made > made.txt"
    assert card["subagent"] == "Make a file"
    assert (tmp_path / "workspace" / "made.txt").exists()
    result = _code_result(socket)
    assert "ran it" in result and "made.txt" in result
    [task] = SubAgentTaskStore(tmp_path / "state").list_for_thread("t1")
    assert (task.status, task.model, task.tool_uses) == ("succeeded", "codex:model", 1)
    kinds = [e["kind"] for e in get_subagent_transcript(task.task_id)["entries"]]  # type: ignore[index]
    assert kinds == ["user", "tool", "agent"]


async def test_nothing_the_run_says_or_spends_reaches_the_parents_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("approve")))
    socket = _Socket(answer=lambda payload: True)

    await _run(session, socket)

    deltas = "".join(p["text"] for p in socket.of("agent_delta"))
    assert "ran it" not in deltas
    assert deltas == "done"
    assert socket.of("usage") == []


async def test_a_declined_command_does_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("approve")))
    socket = _Socket(answer=lambda payload: False)

    await _run(session, socket)

    assert not (tmp_path / "workspace" / "made.txt").exists()
    assert "was declined" in _code_result(socket)


async def test_plan_mode_declines_the_run_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("approve")))
    session._toggle_mode("plan")
    socket = _Socket(answer=lambda payload: True)

    await _run(session, socket)

    assert socket.of("approval_required") == []
    assert not (tmp_path / "workspace" / "made.txt").exists()


async def test_a_plain_read_of_the_folder_runs_without_a_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("read")))
    socket = _Socket(answer=lambda payload: False)

    await _run(session, socket)

    assert socket.of("approval_required") == []
    assert "ran it" in _code_result(socket)


async def test_a_model_codex_cant_use_gets_a_reason_and_no_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(
        tmp_path,
        monkeypatch,
        codex,
        _Model(responses=_replies("approve")),
        default_model="anthropic:claude",
    )
    socket = _Socket(answer=lambda payload: True)

    await _run(session, socket)

    assert "Responses API" in _code_result(socket)
    assert "Settings > Code" in _code_result(socket)
    assert SubAgentTaskStore(tmp_path / "state").list_for_thread("t1") == []


async def test_the_code_model_setting_stands_in_for_a_chat_model_codex_cant_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(
        tmp_path,
        monkeypatch,
        codex,
        _Model(responses=_replies("approve")),
        default_model="anthropic:claude",
        code_model="fake:model",
    )
    socket = _Socket(answer=lambda payload: True)

    await _run(session, socket)

    [record] = SubAgentTaskStore(tmp_path / "state").list_for_thread("t1")
    assert (record.status, record.model) == ("succeeded", "codex:model")


async def test_stop_ends_the_run_in_codex_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("hang")))
    socket = _Socket()
    turn = asyncio.create_task(_run(session, socket))
    log = tmp_path / "requests.jsonl"
    for _ in range(100):
        if log.exists() and "turn/start" in log.read_text():
            break
        await asyncio.sleep(0.05)

    session.request_stop()
    await asyncio.wait_for(turn, 20)

    # The run finishes stopping on its own -- Codex is interrupted and its
    # commands ended -- after the parent's turn has already returned.
    store = SubAgentTaskStore(tmp_path / "state")
    for _ in range(100):
        [task] = store.list_for_thread("t1")
        if task.status != "running":
            break
        await asyncio.sleep(0.05)
    assert task.status == "stopped"
    methods = [json.loads(line).get("method") for line in log.read_text().splitlines()]
    assert "turn/interrupt" in methods


async def test_stopping_a_run_from_the_panel_leaves_no_card_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("approve")))
    socket = _Socket()
    turn = asyncio.create_task(_run(session, socket))
    for _ in range(100):
        if socket.of("approval_required"):
            break
        await asyncio.sleep(0.05)
    [task] = SubAgentTaskStore(tmp_path / "state").list_for_thread("t1")

    stop_subagent_task(tmp_path / "state", task.task_id)
    await asyncio.wait_for(turn, 20)

    assert session._pending_approvals == {}
    assert SubAgentTaskStore(tmp_path / "state").load(task.task_id).status == "stopped"  # type: ignore[union-attr]
    assert not (tmp_path / "workspace" / "made.txt").exists()


async def test_each_command_asks_unless_the_task_was_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("twice")))
    socket = _Socket(answer=lambda payload: True)

    await _run(session, socket)

    assert len(socket.of("approval_required")) == 2


async def test_allowing_a_kind_of_action_for_the_task_stops_the_cards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("twice")))
    socket = _Socket(answer=lambda payload: True, scope="task")

    await _run(session, socket)

    assert len(socket.of("approval_required")) == 1
    assert (tmp_path / "workspace" / "made2.txt").exists()
    [(_, action)] = session._code_task_allowances
    assert action == "run_code_command"


def _always_allow(tmp_path: Path, **policies: str) -> None:
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "code_permissions.json").write_text(json.dumps(policies))


async def test_always_allowing_commands_in_settings_runs_them_without_cards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    _always_allow(tmp_path, run_code_command="allow")
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("twice")))
    socket = _Socket(answer=lambda payload: False)

    await _run(session, socket)

    assert socket.of("approval_required") == []
    assert (tmp_path / "workspace" / "made2.txt").exists()


async def test_always_allowing_commands_leaves_file_changes_to_ask(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    _always_allow(tmp_path, run_code_command="allow")
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("file")))
    socket = _Socket(answer=lambda payload: True)

    await _run(session, socket)

    assert [c["tool_name"] for c in socket.of("approval_required")] == ["apply_code_change"]


async def test_plan_mode_still_declines_what_settings_always_allow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    _always_allow(tmp_path, run_code_command="allow")
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("approve")))
    session._toggle_mode("plan")
    socket = _Socket(answer=lambda payload: True)

    await _run(session, socket)

    assert not (tmp_path / "workspace" / "made.txt").exists()


async def test_the_settings_switch_does_not_reach_a_scheduled_run_with_its_own_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    _always_allow(tmp_path, run_code_command="allow")
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("approve")))
    # "auto" approves local file edits and leaves running code to a person.
    session.run_approval_mode = "auto"
    socket = _Socket(answer=lambda payload: False)

    await _run(session, socket)

    assert [c["tool_name"] for c in socket.of("approval_required")] == ["run_code_command"]
    assert not (tmp_path / "workspace" / "made.txt").exists()


def _records(tmp_path: Path) -> list[Any]:
    return sorted(
        SubAgentTaskStore(tmp_path / "state").list_for_thread("t1"), key=lambda r: r.started_at
    )


async def test_a_follow_up_carries_on_in_the_same_codex_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    first = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("count one")))
    first_socket = _Socket()
    await _run(first, first_socket)
    [earlier] = _records(tmp_path)

    second = _session(
        tmp_path,
        monkeypatch,
        codex,
        _Model(responses=_replies("count two", continue_task=earlier.task_id)),
    )
    second_socket = _Socket()
    await _run(second, second_socket)
    _, later = _records(tmp_path)

    assert "turn 1" in _code_result(first_socket)
    assert f'continue_task="{earlier.task_id}"' in _code_result(first_socket)
    # The same thread, so it is the second turn of it.
    assert "turn 2" in _code_result(second_socket)
    assert (later.continues, later.codex_thread) == (earlier.task_id, earlier.codex_thread)
    assert earlier.codex_thread and earlier.continues == ""
    # The earlier record is left as the run it was.
    assert (earlier.status, earlier.result) == ("succeeded", "turn 1")


async def test_a_follow_up_to_a_thread_codex_lost_is_given_the_earlier_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    session = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("count one")))
    await _run(session, _Socket())
    [earlier] = _records(tmp_path)
    earlier.codex_thread = "thr-gone"
    SubAgentTaskStore(tmp_path / "state").save(earlier)

    again = _session(
        tmp_path,
        monkeypatch,
        codex,
        _Model(responses=_replies("count two", continue_task=earlier.task_id)),
    )
    await _run(again, _Socket())

    sent = [
        json.loads(line)
        for line in (tmp_path / "requests.jsonl").read_text().splitlines()
        if '"turn/start"' in line
    ]
    prompt = sent[-1]["params"]["input"][0]["text"]
    assert "count one" in prompt and "turn 1" in prompt and prompt.endswith("count two")
    assert _records(tmp_path)[-1].codex_thread != "thr-gone"


@pytest.mark.parametrize("target", ["nope", "other-conversation", "running", "never-started"])
async def test_a_follow_up_needs_a_finished_code_task_of_this_conversation(
    target: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, codex: CodexHost
) -> None:
    first = _session(tmp_path, monkeypatch, codex, _Model(responses=_replies("count one")))
    await _run(first, _Socket())
    [earlier] = _records(tmp_path)
    store = SubAgentTaskStore(tmp_path / "state")
    if target == "other-conversation":
        earlier.thread_id = "t2"
    elif target == "running":
        earlier.status = "running"
        # A run this process is driving, as opposed to one a restart cut off.
        monkeypatch.setitem(
            subagent_tasks._RUNNING, earlier.task_id, asyncio.get_running_loop().create_future()
        )
    elif target == "never-started":
        earlier.codex_thread = ""
    store.save(earlier)
    wanted = "nope" if target == "nope" else earlier.task_id

    again = _session(
        tmp_path, monkeypatch, codex, _Model(responses=_replies("count two", continue_task=wanted))
    )
    socket = _Socket()
    await _run(again, socket)

    assert "turn 2" not in _code_result(socket)
    assert len(store.list_for_thread("t1")) == (0 if target == "other-conversation" else 1)
    assert [r for r in store.list_for_thread("t1") if r.continues] == []

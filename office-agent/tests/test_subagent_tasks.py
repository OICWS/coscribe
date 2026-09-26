# ruff: noqa: E402
"""Tests for delegated sub-agents: runtime_lg/subagents.py's runner and
spawn tools, and tools/subagent_tasks.py's store and stop. A fake model
drives a real LangGraph agent; the host's decide() stands in for the
session's approval policy.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, ToolCall
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from coscribe.runtime.types import tool_metadata
from coscribe.runtime_lg.selfwake import poll_due_wakes
from coscribe.runtime_lg.subagents import SubAgentHost, build_delegation_tools
from coscribe.tools import subagent_tasks
from coscribe.tools.selfwake import WakeRequest, WakeStore
from coscribe.tools.subagent_tasks import (
    SubAgentTask,
    SubAgentTaskStore,
    get_subagent_transcript,
    stop_subagent_task,
)


class FakeToolCallingChatModel(BaseChatModel):
    """Same shape as test_selfwake_resume.py's identical class."""

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

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        message = self.responses[self.i]
        self.i += 1
        chunk = AIMessageChunk(content=message.content or "", tool_calls=message.tool_calls)
        yield ChatGenerationChunk(message=chunk)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling-chat-model"


async def _wait_for_status(
    store: SubAgentTaskStore, task_id: str, status: str, *, timeout: float = 5.0
) -> SubAgentTask:
    async def _poll() -> SubAgentTask:
        while True:
            task = store.load(task_id)
            if task is not None and task.status == status:
                return task
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(_poll(), timeout=timeout)


def _pretend_running(monkeypatch: pytest.MonkeyPatch, task_id: str) -> None:
    runner = asyncio.get_running_loop().create_future()
    monkeypatch.setitem(subagent_tasks._RUNNING, task_id, runner)


async def test_a_run_cut_off_by_a_restart_reads_as_stopped(tmp_path: Path) -> None:
    SubAgentTaskStore(tmp_path).save(
        SubAgentTask(
            task_id="orphan",
            thread_id="thread-1",
            instructions="",
            prompt="p",
            tool_names="",
            description="d",
            status="needs_approval",
            started_at=datetime.now(UTC).isoformat(),
            pending_approval={"id": "gone"},
        )
    )

    [task] = SubAgentTaskStore(tmp_path).list_for_thread("thread-1")

    assert task.status == "stopped"
    assert task.pending_approval is None
    assert task.error == "The app restarted while it was running."


def _host(
    tmp_path: Path,
    model: Any,
    decide: Any = None,
    configured: tuple[str, ...] = ("fake:model",),
) -> SubAgentHost:
    changes: list[str] = []

    async def changed(task: SubAgentTask) -> None:
        changes.append(task.status)

    def resolve_model(requested: str) -> tuple[str, Any]:
        if requested and requested not in configured:
            raise ValueError(f"{requested!r} isn't a configured model")
        return requested or "fake:model", model

    async def never_asked(request: dict[str, Any], task: SubAgentTask) -> Any:
        raise AssertionError("nothing here needs a decision")

    return SubAgentHost(
        thread_id="thread-1",
        state_dir=tmp_path,
        resolve_model=resolve_model,
        configured_models=lambda: list(configured),
        decide=decide or never_asked,
        changed=changed,
    )


def _read_note() -> str:
    """Read the note."""
    return "the note says hi"


def _write_note(text: str) -> str:
    """Write the note."""
    return f"wrote {text}"


def _tools() -> list[Any]:
    return [
        tool_metadata(_read_note, risk_category="READ", category="files"),
        tool_metadata(_write_note, risk_category="WRITE_LOCAL", category="files"),
    ]


def _call(name: str, args: dict[str, Any], call_id: str = "c1") -> ToolCall:
    return ToolCall(name=name, args=args, id=call_id)


def _spawn_tools(host: SubAgentHost) -> tuple[Any, Any]:
    spawn, spawn_background = build_delegation_tools(host, _tools())
    return spawn, spawn_background


async def test_a_background_run_reports_its_progress_and_result(tmp_path: Path) -> None:
    model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_call("_read_note", {})]),
            AIMessage(content="it says hi"),
        ]
    )
    _, spawn_background = _spawn_tools(_host(tmp_path, model))

    started = await spawn_background(description="read the note", prompt="what does it say?")
    store = SubAgentTaskStore(tmp_path)
    task = await _wait_for_status(store, started["task_id"], "succeeded")

    assert task.result == "it says hi"
    assert task.model == "fake:model"
    assert task.tool_uses == 1
    assert task.last_tool == {"tool_name": "_read_note", "arguments": {}}
    assert task.background is True
    kinds = [e["kind"] for e in get_subagent_transcript(task.task_id)["entries"]]
    assert kinds == ["user", "tool", "agent"]


async def test_spawn_agent_waits_for_the_report(tmp_path: Path) -> None:
    model = FakeToolCallingChatModel(responses=[AIMessage(content="done: 42")])
    spawn, _ = _spawn_tools(_host(tmp_path, model))

    report = await spawn(description="count", prompt="count things", tool_names="_read_note")

    assert report == "done: 42"
    [task] = SubAgentTaskStore(tmp_path).list_for_thread("thread-1")
    assert task.status == "succeeded"
    assert task.background is False


async def test_a_gated_call_waits_for_the_hosts_decision(tmp_path: Path) -> None:
    model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_call("_write_note", {"text": "hi"})]),
            AIMessage(content="couldn't write it"),
        ]
    )
    asked: list[dict[str, Any]] = []

    async def decide(request: dict[str, Any], task: SubAgentTask) -> Any:
        asked.append(request)
        task.status = "needs_approval"
        task.pending_approval = {"id": "r1", "tool_name": request["name"]}
        return {"type": "reject", "message": "not now"}

    spawn, _ = _spawn_tools(_host(tmp_path, model, decide))
    report = await spawn(description="write", prompt="write hi")

    assert [r["name"] for r in asked] == ["_write_note"]
    assert report == "couldn't write it"
    [task] = SubAgentTaskStore(tmp_path).list_for_thread("thread-1")
    assert task.status == "succeeded"
    assert task.pending_approval is None
    entries = get_subagent_transcript(task.task_id)["entries"]
    tool_entry = next(e for e in entries if e["kind"] == "tool")
    assert "not now" in str(tool_entry["result"])


async def test_stopping_a_waited_on_sub_agent_hands_the_parent_a_report(tmp_path: Path) -> None:
    release = asyncio.Event()

    async def decide(request: dict[str, Any], task: SubAgentTask) -> Any:
        await release.wait()
        return {"type": "approve"}

    model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[_call("_write_note", {"text": "x"})])]
    )
    spawn, _ = _spawn_tools(_host(tmp_path, model, decide))
    parent = asyncio.create_task(spawn(description="write", prompt="write x"))
    store = SubAgentTaskStore(tmp_path)
    while not store.list_for_thread("thread-1"):
        await asyncio.sleep(0.01)
    [task] = store.list_for_thread("thread-1")
    await asyncio.sleep(0.05)

    stop_subagent_task(tmp_path, task.task_id)
    report = await asyncio.wait_for(parent, timeout=5)

    assert report == "(the user stopped this sub-agent before it finished)"
    assert store.load(task.task_id).status == "stopped"


async def test_an_unconfigured_model_is_refused(tmp_path: Path) -> None:
    model = FakeToolCallingChatModel(responses=[])
    spawn, _ = _spawn_tools(_host(tmp_path, model))

    with pytest.raises(ValueError, match="isn't a configured model"):
        await spawn(description="x", prompt="y", model="nope:model")
    assert SubAgentTaskStore(tmp_path).list_for_thread("thread-1") == []


async def test_a_run_waiting_on_approval_does_not_fire_its_wake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pretend_running(monkeypatch, "sa-3")
    SubAgentTaskStore(tmp_path).save(
        SubAgentTask(
            task_id="sa-3",
            thread_id="thread-1",
            instructions="",
            prompt="y",
            tool_names="",
            description="asking",
            status="needs_approval",
            started_at=datetime.now(UTC).isoformat(),
        )
    )
    WakeStore(tmp_path).save(
        WakeRequest(
            wake_id="wake-sa-3",
            thread_id="thread-1",
            kind="subagent",
            reason="tell me when it's done",
            created_at=datetime.now(UTC).isoformat(),
            status="pending",
            subagent_task_id="sa-3",
        )
    )

    async def get_session(thread_id: str) -> Any:
        raise AssertionError("should not wake")

    assert await poll_due_wakes(tmp_path, get_session) == []


def test_records_from_before_stop_replaced_pause_still_load() -> None:
    task = SubAgentTask.from_dict(
        {
            "task_id": "old",
            "thread_id": "t",
            "instructions": "",
            "prompt": "p",
            "description": "d",
            "status": "paused",
            "started_at": "2026-09-01T00:00:00+00:00",
        }
    )
    assert task.status == "stopped"
    assert task.model == ""


async def test_subagent_wake_fires_once_the_task_is_no_longer_running(tmp_path: Path) -> None:
    task_store = SubAgentTaskStore(tmp_path)
    task_store.save(
        SubAgentTask(
            task_id="sa-1",
            thread_id="thread-1",
            instructions="x",
            prompt="y",
            tool_names="",
            description="a finished sub-agent",
            status="succeeded",
            started_at=datetime.now(UTC).isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            result="ok",
        )
    )
    wake_store = WakeStore(tmp_path)
    wake_store.save(
        WakeRequest(
            wake_id="wake-sa-1",
            thread_id="thread-1",
            kind="subagent",
            reason="tell me when it's done",
            created_at=datetime.now(UTC).isoformat(),
            status="pending",
            subagent_task_id="sa-1",
        )
    )

    calls: list[str] = []

    async def get_session(thread_id: str) -> Any:
        calls.append(thread_id)

        class _Session:
            async def handle_user_message(self, text: str, socket: Any) -> None:
                pass

        return _Session()

    fired = await poll_due_wakes(tmp_path, get_session)

    assert [w.wake_id for w in fired] == ["wake-sa-1"]
    assert calls == ["thread-1"]


async def test_subagent_wake_stays_pending_while_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _pretend_running(monkeypatch, "sa-2")
    task_store = SubAgentTaskStore(tmp_path)
    task_store.save(
        SubAgentTask(
            task_id="sa-2",
            thread_id="thread-1",
            instructions="x",
            prompt="y",
            tool_names="",
            description="still going",
            status="running",
            started_at=datetime.now(UTC).isoformat(),
        )
    )
    wake_store = WakeStore(tmp_path)
    wake_store.save(
        WakeRequest(
            wake_id="wake-sa-2",
            thread_id="thread-1",
            kind="subagent",
            reason="tell me when it's done",
            created_at=datetime.now(UTC).isoformat(),
            status="pending",
            subagent_task_id="sa-2",
        )
    )

    async def get_session(thread_id: str) -> Any:
        raise AssertionError("should not resume a thread for a still-pending wake")

    fired = await poll_due_wakes(tmp_path, get_session)

    assert fired == []
    resolved = wake_store.load("wake-sa-2")
    assert resolved is not None
    assert resolved.status == "pending"

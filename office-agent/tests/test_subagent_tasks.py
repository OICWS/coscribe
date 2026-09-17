# ruff: noqa: E402
"""Tests for the Sub Agents feature's background half: runtime_lg/
subagents.py's build_spawn_agent_background_tool, and tools/
subagent_tasks.py's supervisor/store/pause-resume/transcript machinery
underneath it. Same FakeToolCallingChatModel pattern as tests/
test_selfwake_resume.py -- drives a real LangGraph agent, no live LLM.

Pause is tested by requesting it *before* the supervisor coroutine has
had a chance to run at all (immediately after the tool call returns,
which is also immediately after register_live_subagent runs
synchronously) -- deterministic and step-count-independent: the
supervisor's very first loop iteration sees pause_requested=True before
ever invoking the model, so there's no race to win against a real
model call landing first.
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
from coscribe.runtime_lg.subagents import build_spawn_agent_background_tool
from coscribe.tools.selfwake import WakeRequest, WakeStore
from coscribe.tools.subagent_tasks import (
    SubAgentTask,
    SubAgentTaskStore,
    get_subagent_transcript,
    pause_subagent_task,
    resume_subagent_task,
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


async def test_background_subagent_runs_to_completion(tmp_path: Path) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="hello!")])
    spawn_agent_background = build_spawn_agent_background_tool(
        fake_model, [], thread_id="thread-1", state_dir=tmp_path
    )

    result = await spawn_agent_background(
        instructions="You are a helper.", prompt="say hi", description="say hi"
    )
    assert result["status"] == "running"
    task_id = result["task_id"]

    store = SubAgentTaskStore(tmp_path)
    task = await _wait_for_status(store, task_id, "succeeded")
    assert task.result == "hello!"
    assert fake_model.i == 1

    transcript = get_subagent_transcript(task_id)
    assert transcript is not None
    kinds = [entry["kind"] for entry in transcript["entries"]]
    assert kinds == ["user", "agent"]
    assert transcript["entries"][0]["text"] == "say hi"
    assert transcript["entries"][1]["text"] == "hello!"


async def test_pause_before_any_step_then_resume_completes(tmp_path: Path) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="done")])
    spawn_agent_background = build_spawn_agent_background_tool(
        fake_model, [], thread_id="thread-1", state_dir=tmp_path
    )

    result = await spawn_agent_background(
        instructions="You are a helper.", prompt="do the thing", description="a task"
    )
    task_id = result["task_id"]
    store = SubAgentTaskStore(tmp_path)

    # Pause immediately -- registered synchronously inside the tool call
    # itself (register_live_subagent), so this is guaranteed to land
    # before the supervisor's own asyncio.Task has had a chance to run
    # even its first loop iteration.
    pause_subagent_task(tmp_path, task_id)
    task = await _wait_for_status(store, task_id, "paused")
    assert fake_model.i == 0  # the model was never actually called

    resume_subagent_task(tmp_path, task_id)
    task = await _wait_for_status(store, task_id, "succeeded")
    assert task.result == "done"
    assert fake_model.i == 1


async def test_pause_requires_a_live_handle(tmp_path: Path) -> None:
    store = SubAgentTaskStore(tmp_path)
    store.save(
        SubAgentTask(
            task_id="orphan-1",
            thread_id="thread-1",
            instructions="x",
            prompt="y",
            tool_names="",
            description="orphaned by a restart",
            status="running",
            started_at=datetime.now(UTC).isoformat(),
        )
    )
    with pytest.raises(RuntimeError):
        pause_subagent_task(tmp_path, "orphan-1")


async def test_background_subagent_blocked_on_approval_not_bridged(tmp_path: Path) -> None:
    """A background sub-agent's own tool call that needs approval can't
    be bridged up the way the synchronous spawn_agent does (see tools/
    subagent_tasks.py's module docstring) -- it should end up durably
    "blocked_on_approval" instead of hanging or silently approving
    itself."""

    def write_note(text: str) -> str:
        """Write a short note."""
        return f"wrote: {text}"

    risky_tool = tool_metadata(write_note, risk_category="WRITE_LOCAL")
    call = ToolCall(name="write_note", args={"text": "hi"}, id="call_1")
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="", tool_calls=[call])])
    spawn_agent_background = build_spawn_agent_background_tool(
        fake_model, [risky_tool], thread_id="thread-1", state_dir=tmp_path
    )

    result = await spawn_agent_background(
        instructions="You are a helper.",
        prompt="write a note",
        description="write a note",
        tool_names="write_note",
    )
    task_id = result["task_id"]
    store = SubAgentTaskStore(tmp_path)
    task = await _wait_for_status(store, task_id, "blocked_on_approval")
    assert task.result is None


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


async def test_subagent_wake_stays_pending_while_still_running(tmp_path: Path) -> None:
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

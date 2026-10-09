# ruff: noqa: E402
"""Web tests: sub-agents and their approvals."""

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from coscribe.config import Settings
from coscribe.tools.subagent_tasks import SubAgentTaskStore

from .helpers import (
    ConcurrentSpawnFakeModel,
    FakeToolCallingChatModel,
    _client_lg,
    _receive_until,
    _start_mode,
    _tool_call,
    _verdict,
    _write_call,
)


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


def test_reviewer_tools_are_exactly_the_read_only_documents_and_file_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session.py's _build_lg_tools hands review_work's reviewer exactly
    the tools with category=="documents" and requires_approval==False,
    plus the read-only file tools (a reviewer that can't list the folder
    guesses file names and "reviews" files that don't exist) --
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

    settings = Settings(
        _env_file=None,
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        default_model="gemini:gemini-flash-latest",
    )
    agent = build_coordinator_agent(settings, "test-thread")
    from coscribe.runtime_lg import select_reviewer_tools

    reviewer_tools = select_reviewer_tools(agent.tools)
    names = sorted(t.__name__ if hasattr(t, "__name__") else t.name for t in reviewer_tools)
    assert names == [
        "check_pptx_delivery",
        "get_file_info",
        "list_files",
        "list_pptx_animation_types",
        "list_pptx_icons",
        "list_pptx_shape_types",
        "list_pptx_shapes",
        "list_pptx_transition_types",
        "read_docx",
        "read_file",
        "read_pdf",
        "read_pptx",
        "read_pptx_theme_colors",
        "read_pptx_xml",
        "read_xlsx",
        "render_pptx_preview",
        "search_pdf",
    ]


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

    from coscribe.conversation.session import _SubAgentApprovalChannel
    from coscribe.tools import subagent_tasks
    from coscribe.tools.subagent_tasks import SubAgentTask

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


class RoutedChatModel(FakeToolCallingChatModel):
    """Answers by who is asking -- the parent, a sub-agent or the Auto
    reviewer -- since a background sub-agent's calls interleave with the
    parent's in no fixed order."""

    route: Any = None

    def _next(self, messages: list[BaseMessage]) -> AIMessage:
        self.received.append(list(messages))
        return self.route(messages)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next(messages))])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        message = self._next(messages)
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=message.content or "", tool_calls=message.tool_calls)
        )


def test_a_background_sub_agent_is_reviewed_against_its_task_and_reports_back_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    child_prompt = "Write hi to note.txt."
    reviews: list[str] = []

    def route(messages: list[BaseMessage]) -> AIMessage:
        system = str(messages[0].content) if messages[0].type == "system" else ""
        last = messages[-1]
        if "You review one action" in system:
            reviews.append(str(last.content))
            return _verdict("allow", "the delegated task asks for this note")
        if "delegated to you by another assistant" in system:
            if last.type == "tool":
                return AIMessage(content="wrote note.txt")
            return AIMessage(content="", tool_calls=[_write_call("w1", "note.txt")])
        if last.type == "human" and "[Sub-agent finished]" in str(last.content):
            return AIMessage(content="The sub-agent wrote the note.")
        if last.type == "tool":
            return AIMessage(content="Handed to a sub-agent.")
        return AIMessage(
            content="",
            tool_calls=[
                _tool_call(
                    "s1",
                    "spawn_agent_background",
                    {"description": "write the note", "prompt": child_prompt},
                )
            ],
        )

    fake_model = RoutedChatModel(responses=[], route=route)
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_sub_report") as ws:
            _start_mode(ws, "/auto")
            ws.send_json(
                {"type": "user_message", "text": "Use a sub-agent to write the note, not yourself."}
            )
            _receive_until(ws, "tasks_changed")
            started = _receive_until(ws, "turn_started")[-1]
            reply = _receive_until(ws, "agent_message")[-1]

    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"
    assert "handed part of the work to" in reviews[0]
    assert child_prompt in reviews[0]
    assert started["text"].startswith('[Sub-agent finished] "write the note"')
    assert "wrote note.txt" in started["text"]
    assert reply["text"] == "The sub-agent wrote the note."


def test_the_sub_agent_endpoints_refuse_an_id_that_is_a_path_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        transcript = client.get("/api/subagents/..%5Coutside/transcript")
        stop = client.post("/api/subagents/..%5Coutside/stop")

    assert transcript.status_code == 404
    assert stop.status_code == 404

# ruff: noqa: E402
"""Tests for runtime_lg/selfwake.py's poll_due_wakes -- the resume half of
Phase 4's suspend/resume primitives (tools/selfwake.py holds the create/
list/cancel half, covered by tests/test_selfwake_tool.py instead).

Builds a real ChatSessionLG against a real (tmp-file) AsyncSqliteSaver
checkpointer and a scripted fake chat model, same pattern
tests/test_web.py's FakeToolCallingChatModel/_client_lg use -- this file
skips the FastAPI/WebSocket layer entirely and drives ChatSessionLG plus
poll_due_wakes directly, since what's under test is the poll-and-resume
logic itself, not the web transport around it.
"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from coscribe.config import Settings
from coscribe.runtime_lg.selfwake import poll_due_wakes
from coscribe.tools.background_tasks import BackgroundTask, BackgroundTaskStore
from coscribe.tools.selfwake import WakeRequest, WakeStore

# Import order matters here: web/session.py does `from ..cli import
# INIT_PROMPT`, and cli.py does `from .web.session import ChatSessionLG` --
# importing coscribe.cli first (as web/app.py itself does) resolves that
# circular import safely; importing coscribe.web.session directly first
# does not (see test_web.py's identical `from coscribe.web.app import ...`
# for the same reason).
from coscribe.web.app import ChatSessionLG


class FakeToolCallingChatModel(BaseChatModel):
    """Same shape as test_web.py's identical class -- cycles through
    `responses` in order, streaming each as a single AIMessageChunk."""

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


class _FakeContextWindowClient:
    def get_context_window(self, model: str) -> int:
        return 128_000


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        default_model="fake:model",
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        skills_dir=tmp_path / "skills",
        memory_path=tmp_path / "MEMORY.md",
    )  # type: ignore[arg-type]


async def _make_get_session(
    settings: Settings, checkpointer: Any, fake_model: FakeToolCallingChatModel, monkeypatch: Any
) -> Any:
    monkeypatch.setattr("coscribe.web.session.resolve_chat_model", lambda *a, **k: fake_model)

    async def get_session(thread_id: str) -> ChatSessionLG:
        return ChatSessionLG(
            thread_id=thread_id,
            settings=settings,
            context_window_client=_FakeContextWindowClient(),  # type: ignore[arg-type]
            custom_providers={},
            extra_tools=[],
            checkpointer=checkpointer,
        )

    return get_session


async def test_past_due_timer_wake_resumes_its_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="good morning!")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        wake_store = WakeStore(settings.state_dir)
        wake_store.save(
            WakeRequest(
                wake_id="wake-1",
                thread_id="thread-1",
                kind="timer",
                reason="say good morning",
                created_at=datetime.now(UTC).isoformat(),
                status="pending",
                wake_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            )
        )

        fired = await poll_due_wakes(settings.state_dir, get_session)

    assert [w.wake_id for w in fired] == ["wake-1"]
    assert fake_model.i == 1  # a real turn ran
    resolved = wake_store.load("wake-1")
    assert resolved is not None
    assert resolved.status == "woken"
    assert resolved.woken_at is not None


async def test_future_timer_wake_stays_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="too early")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        wake_store = WakeStore(settings.state_dir)
        wake_store.save(
            WakeRequest(
                wake_id="wake-2",
                thread_id="thread-1",
                kind="timer",
                reason="not yet",
                created_at=datetime.now(UTC).isoformat(),
                status="pending",
                wake_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            )
        )

        fired = await poll_due_wakes(settings.state_dir, get_session)

    assert fired == []
    assert fake_model.i == 0  # no turn ran
    resolved = wake_store.load("wake-2")
    assert resolved is not None
    assert resolved.status == "pending"


async def test_wake_hitting_a_gated_tool_call_resolves_promptly_not_hangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real bug found via live testing: a wake-
    triggered turn that hits an approval-gated tool call (write_file is
    WRITE_LOCAL, requires approval) used to hang the calling coroutine
    forever -- _resolve_pending_approvals would create a real
    asyncio.Future and await it, resolved only by a genuine incoming WS
    message that _SilentSocket (nobody is watching) can never send. For a
    poll-loop caller like poll_due_wakes, that wedges the *entire*
    background poller on the very first unattended wake that happens to
    touch a gated tool, not just this one thread. Fixed by
    _can_resolve_approvals's check in web/session.py, skipping
    _resolve_pending_approvals entirely for a silent socket and leaving
    the interrupt durably paused in the checkpointer instead (same state
    resume_after_reconnect already knows how to pick up later). Wrapped in
    asyncio.wait_for so a regression fails loudly with a TimeoutError
    instead of hanging the test suite itself."""
    from langchain_core.messages import ToolCall

    settings = _settings(tmp_path)
    call = ToolCall(name="write_file", args={"path": "note.txt", "content": "hi"}, id="call_1")
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="", tool_calls=[call])])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        wake_store = WakeStore(settings.state_dir)
        wake_store.save(
            WakeRequest(
                wake_id="wake-5",
                thread_id="thread-1",
                kind="timer",
                reason="write a note",
                created_at=datetime.now(UTC).isoformat(),
                status="pending",
                wake_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            )
        )

        fired = await asyncio.wait_for(poll_due_wakes(settings.state_dir, get_session), timeout=5)

    assert [w.wake_id for w in fired] == ["wake-5"]
    # The gated write_file call was never approved -- the file must not exist.
    assert not (tmp_path / "workspace" / "note.txt").exists()


@pytest.mark.parametrize(("decision", "written"), [("allow", True), ("block", False)])
async def test_unattended_wake_in_auto_mode_lets_the_reviewer_decide(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, decision: str, written: bool
) -> None:
    import json

    from langchain_core.messages import ToolCall

    settings = _settings(tmp_path)
    call = ToolCall(name="write_file", args={"path": "note.txt", "content": "hi"}, id="call_1")
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content=json.dumps({"decision": decision, "reason": "because"})),
            AIMessage(content="done"),
        ]
    )
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        make_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)

        async def get_session(thread_id: str) -> ChatSessionLG:
            session = await make_session(thread_id)
            session.auto_mode = True
            return session

        WakeStore(settings.state_dir).save(
            WakeRequest(
                wake_id="wake-auto",
                thread_id="thread-1",
                kind="timer",
                reason="write a note",
                created_at=datetime.now(UTC).isoformat(),
                status="pending",
                wake_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
            )
        )

        fired = await asyncio.wait_for(poll_due_wakes(settings.state_dir, get_session), timeout=5)

    assert [w.wake_id for w in fired] == ["wake-auto"]
    assert (tmp_path / "workspace" / "note.txt").exists() is written
    assert fake_model.i == 3


async def test_task_wake_fires_once_the_background_task_is_no_longer_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="it finished")])
    task_store = BackgroundTaskStore(settings.state_dir)
    task_store.save(
        BackgroundTask(
            task_id="task-1",
            thread_id="thread-1",
            language="python",
            description="crunch some numbers",
            status="succeeded",
            started_at=datetime.now(UTC).isoformat(),
            finished_at=datetime.now(UTC).isoformat(),
            exit_code=0,
        )
    )
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        wake_store = WakeStore(settings.state_dir)
        wake_store.save(
            WakeRequest(
                wake_id="wake-6",
                thread_id="thread-1",
                kind="task",
                reason="tell me when it's done",
                created_at=datetime.now(UTC).isoformat(),
                status="pending",
                task_id="task-1",
            )
        )

        fired = await poll_due_wakes(settings.state_dir, get_session)

    assert [w.wake_id for w in fired] == ["wake-6"]
    assert fake_model.i == 1


async def test_task_wake_stays_pending_while_the_task_is_still_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="still going")])
    task_store = BackgroundTaskStore(settings.state_dir)
    task_store.save(
        BackgroundTask(
            task_id="task-2",
            thread_id="thread-1",
            language="python",
            description="a long job",
            status="running",
            started_at=datetime.now(UTC).isoformat(),
        )
    )
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        wake_store = WakeStore(settings.state_dir)
        wake_store.save(
            WakeRequest(
                wake_id="wake-7",
                thread_id="thread-1",
                kind="task",
                reason="tell me when it's done",
                created_at=datetime.now(UTC).isoformat(),
                status="pending",
                task_id="task-2",
            )
        )

        fired = await poll_due_wakes(settings.state_dir, get_session)

    assert fired == []
    assert fake_model.i == 0

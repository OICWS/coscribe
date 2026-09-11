# ruff: noqa: E402
"""Tests for runtime_lg/scheduled_tasks.py's poll_due_scheduled_tasks --
the resume half of the Scheduled Tasks feature (tools/scheduled_tasks.py
holds the create/list/pause/resume/delete half, covered by
tests/test_scheduled_tasks_tool.py instead).

Same pattern test_selfwake_resume.py already established: a real
(tmp-file) AsyncSqliteSaver checkpointer + a scripted fake chat model,
skipping the FastAPI/WebSocket layer entirely -- what's under test is the
poll-and-resume logic itself.
"""

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from coscribe.config import Settings
from coscribe.runtime_lg.scheduled_tasks import poll_due_scheduled_tasks
from coscribe.tools.scheduled_tasks import ScheduledTrigger, ScheduledTriggerStore, ScheduleRule
from coscribe.tools.workflows import Workflow, WorkflowStore

# Import order matters here -- see test_selfwake_resume.py's identical
# comment for why coscribe.web.app must be imported before ChatSessionLG
# is used, to resolve a circular import safely.
from coscribe.web.app import ChatSessionLG


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


async def test_due_recurring_prompt_trigger_fires_and_advances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="here's your summary")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        stale_next_run = (datetime.now() - timedelta(minutes=5)).isoformat()
        store.save(
            ScheduledTrigger(
                trigger_id="trig-1",
                name="Daily summary",
                thread_id="scheduled-trig-1",
                schedule=ScheduleRule(kind="daily", at="09:00"),
                enabled=True,
                created_at=datetime.now().isoformat(),
                next_run_at=stale_next_run,
                prompt="Summarize yesterday",
            )
        )

        fired = await poll_due_scheduled_tasks(settings.state_dir, get_session)

    assert [t.trigger_id for t in fired] == ["trig-1"]
    assert fake_model.i == 1  # a real turn ran
    resolved = store.load("trig-1")
    assert resolved is not None
    assert resolved.enabled is True  # recurring -- stays enabled
    assert resolved.next_run_at is not None
    assert resolved.next_run_at > stale_next_run  # advanced forward, not left stale
    assert resolved.last_run_status == "completed"
    assert resolved.last_run_at is not None


async def test_due_one_time_trigger_fires_and_disables_itself(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="done")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(
            ScheduledTrigger(
                trigger_id="trig-2",
                name="One-off reminder",
                thread_id="scheduled-trig-2",
                schedule=ScheduleRule(kind="once", at="2020-01-01T00:00:00"),
                enabled=True,
                created_at=datetime.now().isoformat(),
                next_run_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
                prompt="Remind me",
            )
        )

        fired = await poll_due_scheduled_tasks(settings.state_dir, get_session)

    assert [t.trigger_id for t in fired] == ["trig-2"]
    resolved = store.load("trig-2")
    assert resolved is not None
    assert resolved.enabled is False
    assert resolved.next_run_at is None


async def test_workflow_backed_trigger_invokes_run_saved_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="workflow ran")])
    WorkflowStore(settings.state_dir).save(
        Workflow(name="nightly-report", mode="agent", summary="Generate the nightly report")
    )
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(
            ScheduledTrigger(
                trigger_id="trig-3",
                name="Nightly report trigger",
                thread_id="scheduled-trig-3",
                schedule=ScheduleRule(kind="daily", at="09:00"),
                enabled=True,
                created_at=datetime.now().isoformat(),
                next_run_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
                workflow_name="nightly-report",
            )
        )

        fired = await poll_due_scheduled_tasks(settings.state_dir, get_session)

    assert [t.trigger_id for t in fired] == ["trig-3"]
    assert fake_model.i == 1  # the workflow's own agent-mode sub-agent actually ran
    resolved = store.load("trig-3")
    assert resolved is not None
    assert resolved.last_run_status == "completed"
    # run_saved_workflow's own side effect: the Workflow record itself
    # tracks its last run too, same as an interactive /runworkflow would.
    workflow = WorkflowStore(settings.state_dir).load("nightly-report")
    assert workflow is not None
    assert workflow.last_run_status == "completed"


async def test_workflow_trigger_needing_approval_fails_promptly_not_hangs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real bug found via live testing against a
    real running coscribe-web + real Gemini: a workflow-backed scheduled
    task whose agent-mode run hits an approval-gated tool call (write_file
    is WRITE_LOCAL, requires approval) used to hang the calling coroutine
    forever -- _resolve_pending_approvals would create a real
    asyncio.Future and await it, resolved only by a genuine incoming WS
    message that _SilentSocket (nobody is watching) can never send. Since
    poll_due_scheduled_tasks fires are awaited sequentially inside
    web/app.py's single shared background poll loop, this wedged the
    *entire* poller -- every other scheduled task and wake, not just this
    one trigger -- on the very first unattended run that happened to
    touch a gated tool. Fixed by _can_resolve_approvals's check in
    web/session.py: a silent-socket run now reports status="failed" with
    an explanatory error instead of hanging, leaving the interrupt
    durably paused in the checkpointer for a human to resolve later by
    opening the trigger's own thread. Wrapped in asyncio.wait_for so a
    regression fails loudly with a TimeoutError instead of hanging the
    test suite itself."""
    from langchain_core.messages import ToolCall

    settings = _settings(tmp_path)
    call = ToolCall(name="write_file", args={"path": "note.txt", "content": "hi"}, id="call_1")
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="", tool_calls=[call])])
    WorkflowStore(settings.state_dir).save(
        Workflow(name="writes-a-file", mode="agent", summary="Write hi to note.txt")
    )
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(
            ScheduledTrigger(
                trigger_id="trig-6",
                name="Needs approval",
                thread_id="scheduled-trig-6",
                schedule=ScheduleRule(kind="daily", at="09:00"),
                enabled=True,
                created_at=datetime.now().isoformat(),
                next_run_at=(datetime.now() - timedelta(minutes=5)).isoformat(),
                workflow_name="writes-a-file",
            )
        )

        fired = await asyncio.wait_for(
            poll_due_scheduled_tasks(settings.state_dir, get_session), timeout=5
        )

    assert [t.trigger_id for t in fired] == ["trig-6"]
    resolved = store.load("trig-6")
    assert resolved is not None
    assert resolved.last_run_status == "failed"
    # The gated write_file call was never approved -- the file must not exist.
    assert not (tmp_path / "workspace" / "note.txt").exists()


async def test_not_yet_due_trigger_stays_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="too early")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        future_next_run = (datetime.now() + timedelta(hours=1)).isoformat()
        store.save(
            ScheduledTrigger(
                trigger_id="trig-4",
                name="Not due yet",
                thread_id="scheduled-trig-4",
                schedule=ScheduleRule(kind="daily", at="09:00"),
                enabled=True,
                created_at=datetime.now().isoformat(),
                next_run_at=future_next_run,
                prompt="p",
            )
        )

        fired = await poll_due_scheduled_tasks(settings.state_dir, get_session)

    assert fired == []
    assert fake_model.i == 0
    resolved = store.load("trig-4")
    assert resolved is not None
    assert resolved.next_run_at == future_next_run
    assert resolved.last_run_at is None


async def test_disabled_trigger_is_never_due_even_if_next_run_at_is_past(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(
            ScheduledTrigger(
                trigger_id="trig-5",
                name="Paused",
                thread_id="scheduled-trig-5",
                schedule=ScheduleRule(kind="daily", at="09:00"),
                enabled=False,
                created_at=datetime.now().isoformat(),
                next_run_at=(datetime.now() - timedelta(days=1)).isoformat(),
                prompt="p",
            )
        )

        fired = await poll_due_scheduled_tasks(settings.state_dir, get_session)

    assert fired == []
    assert fake_model.i == 0

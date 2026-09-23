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
from coscribe.runtime_lg.scheduled_tasks import fire_trigger_now, poll_due_scheduled_tasks
from coscribe.tools.scheduled_tasks import ScheduledTrigger, ScheduledTriggerStore, ScheduleRule

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


# -- fire_trigger_now --


async def test_fire_trigger_now_runs_immediately_without_touching_the_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The detail page's "Run now" action must not disturb the trigger's
    regular schedule -- next_run_at and enabled stay exactly as they were,
    only last_run_at/last_run_status get recorded, so running a daily 9am
    task by hand at 2pm doesn't make it skip tomorrow's real 9am fire."""
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="ran on demand")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        original_next_run = (datetime.now() + timedelta(hours=6)).isoformat()
        store.save(
            ScheduledTrigger(
                trigger_id="trig-7",
                name="Run me now",
                thread_id="scheduled-trig-7",
                schedule=ScheduleRule(kind="daily", at="09:00"),
                enabled=True,
                created_at=datetime.now().isoformat(),
                next_run_at=original_next_run,
                prompt="Do it now",
            )
        )

        result = await fire_trigger_now(settings.state_dir, "trig-7", get_session)

    assert fake_model.i == 1  # a real turn ran
    assert result.last_run_status == "completed"
    assert result.last_run_at is not None
    # Untouched -- this is the whole point of run-now vs. a real poll fire.
    assert result.next_run_at == original_next_run
    assert result.enabled is True
    resolved = store.load("trig-7")
    assert resolved is not None
    assert resolved.next_run_at == original_next_run


async def test_fire_trigger_now_preserves_paused_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="ran while paused")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(
            ScheduledTrigger(
                trigger_id="trig-8",
                name="Paused but runnable",
                thread_id="scheduled-trig-8",
                schedule=ScheduleRule(kind="manual", at=""),
                enabled=False,
                created_at=datetime.now().isoformat(),
                next_run_at=None,
                prompt="Do it now",
            )
        )

        result = await fire_trigger_now(settings.state_dir, "trig-8", get_session)

    assert result.enabled is False  # run-now doesn't implicitly resume it
    assert result.last_run_status == "completed"


async def test_fire_trigger_now_unknown_id_raises_keyerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        with pytest.raises(KeyError):
            await fire_trigger_now(settings.state_dir, "does-not-exist", get_session)


# -- approval_mode / model threading (Workstream C) --


@pytest.mark.parametrize("approval_mode", ["auto", "skip"])
async def test_approval_mode_auto_or_skip_auto_approves_a_gated_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approval_mode: str
) -> None:
    """Unlike the default "manual" mode (where the call would park,
    awaiting an approval nobody is there to give), an "auto"/"skip"
    trigger must actually get the gated write_file call approved and
    executed -- session.accept_edits is set for the
    duration of the fire, which _decide_action_request's own accept_edits
    branch auto-approves before ever reaching the Future-creation code
    that would otherwise need a real approver."""
    from langchain_core.messages import ToolCall

    settings = _settings(tmp_path)
    call = ToolCall(name="write_file", args={"path": "note.txt", "content": "hi"}, id="call_1")
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(
            ScheduledTrigger(
                trigger_id="trig-10",
                name="Auto-approved write",
                thread_id="scheduled-trig-10",
                schedule=ScheduleRule(kind="manual", at=""),
                enabled=True,
                created_at=datetime.now().isoformat(),
                next_run_at=None,
                prompt="Write hi to note.txt",
                approval_mode=approval_mode,
            )
        )

        result = await asyncio.wait_for(
            fire_trigger_now(settings.state_dir, "trig-10", get_session), timeout=5
        )

    assert result.last_run_status == "completed"
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"


async def test_approval_mode_auto_does_not_leak_into_a_later_manual_fire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_fire_trigger_once restores session.accept_edits after firing --
    two separate ScheduledTrigger records sharing nothing don't interact,
    but this also guards the case where the *same* thread_id's session
    object were ever reused across fires (a future in-process session
    cache): approval_mode="auto" on one trigger must not silently leave
    accept_edits on for anyone else driving that same thread afterward."""
    from langchain_core.messages import ToolCall

    settings = _settings(tmp_path)
    call = ToolCall(name="write_file", args={"path": "note.txt", "content": "hi"}, id="call_1")
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        monkeypatch.setattr("coscribe.web.session.resolve_chat_model", lambda *a, **k: fake_model)

        session = ChatSessionLG(
            thread_id="reused-thread",
            settings=settings,
            context_window_client=_FakeContextWindowClient(),  # type: ignore[arg-type]
            custom_providers={},
            extra_tools=[],
            checkpointer=checkpointer,
        )
        trigger = ScheduledTrigger(
            trigger_id="trig-11",
            name="Auto-approved write",
            thread_id="reused-thread",
            schedule=ScheduleRule(kind="manual", at=""),
            enabled=True,
            created_at=datetime.now().isoformat(),
            next_run_at=None,
            prompt="Write hi to note.txt",
            approval_mode="auto",
        )

        from coscribe.runtime_lg.scheduled_tasks import _fire_trigger_once

        await asyncio.wait_for(_fire_trigger_once(trigger, session), timeout=5)
        assert session.accept_edits is False


async def test_trigger_model_override_switches_the_session_before_firing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="ran with override")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(
            ScheduledTrigger(
                trigger_id="trig-12",
                name="Model override",
                thread_id="scheduled-trig-12",
                schedule=ScheduleRule(kind="manual", at=""),
                enabled=True,
                created_at=datetime.now().isoformat(),
                next_run_at=None,
                prompt="Do it",
                model="fake:other-model",
            )
        )

        captured: list[Any] = []

        async def get_session_and_capture(thread_id: str) -> Any:
            session = await get_session(thread_id)
            captured.append(session)
            return session

        result = await fire_trigger_now(settings.state_dir, "trig-12", get_session_and_capture)

    assert result.last_run_status == "completed"
    assert captured[0]._model_string == "fake:other-model"


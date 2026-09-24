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
from coscribe.runtime_lg.scheduled_tasks import (
    _run_in_session,
    fire_trigger_now,
    poll_due_scheduled_tasks,
    reconcile_interrupted_runs,
)
from coscribe.tools.scheduled_tasks import (
    ScheduledRun,
    ScheduledTrigger,
    ScheduledTriggerStore,
    ScheduleRule,
    run_thread_id,
)

# Import order matters here -- see test_selfwake_resume.py's identical
# comment for why coscribe.web.app must be imported before ChatSessionLG
# is used, to resolve a circular import safely.
from coscribe.web.app import ChatSessionLG


class FakeToolCallingChatModel(BaseChatModel):
    """Same shape as test_selfwake_resume.py's identical class."""

    responses: list[AIMessage]
    i: int = 0
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
        chunk = AIMessageChunk(content=message.content or "", tool_calls=message.tool_calls)
        yield ChatGenerationChunk(message=chunk)

    @property
    def _llm_type(self) -> str:
        return "fake-tool-calling-chat-model"


class _FakeContextWindowClient:
    def get_context_window(self, model: str) -> int:
        return 128_000


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        default_model="fake:model",
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        skills_dir=tmp_path / "skills",
        memory_path=tmp_path / "MEMORY.md",
        **overrides,
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


def _trigger(trigger_id: str, **overrides: Any) -> ScheduledTrigger:
    fields: dict[str, Any] = {
        "trigger_id": trigger_id,
        "name": f"Task {trigger_id}",
        "schedule": ScheduleRule(kind="manual", at=""),
        "enabled": True,
        "created_at": datetime.now().isoformat(),
        "next_run_at": None,
        "prompt": "Do it",
    }
    fields.update(overrides)
    return ScheduledTrigger(**fields)


def _human_texts(messages: list[BaseMessage]) -> list[str]:
    return [str(m.content) for m in messages if m.type == "human"]


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
            _trigger(
                "trig-1",
                schedule=ScheduleRule(kind="daily", at="09:00"),
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
    [run] = resolved.runs
    assert run.source == "scheduled"
    assert run.status == "completed"
    assert run.finished_at is not None
    assert run.thread_id == run_thread_id("trig-1", run.run_id)
    assert resolved.last_run_status == "completed"


async def test_each_run_starts_a_fresh_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs never share a thread -- the second run's model input must not
    contain anything from the first."""
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="first answer"), AIMessage(content="second answer")]
    )
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(_trigger("trig-fresh", notes_enabled=False))

        await fire_trigger_now(settings.state_dir, "trig-fresh", get_session)
        await fire_trigger_now(settings.state_dir, "trig-fresh", get_session)

    resolved = store.load("trig-fresh")
    assert resolved is not None
    assert len({run.thread_id for run in resolved.runs}) == 2
    second_input = fake_model.received[-1]
    assert len(_human_texts(second_input)) == 1
    assert not any("first answer" in str(m.content) for m in second_input)


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
            _trigger(
                "trig-2",
                schedule=ScheduleRule(kind="once", at="2020-01-01T00:00:00"),
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
            _trigger(
                "trig-4",
                schedule=ScheduleRule(kind="daily", at="09:00"),
                next_run_at=future_next_run,
            )
        )

        fired = await poll_due_scheduled_tasks(settings.state_dir, get_session)

    assert fired == []
    assert fake_model.i == 0
    resolved = store.load("trig-4")
    assert resolved is not None
    assert resolved.next_run_at == future_next_run
    assert resolved.runs == []


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
            _trigger(
                "trig-5",
                schedule=ScheduleRule(kind="daily", at="09:00"),
                enabled=False,
                next_run_at=(datetime.now() - timedelta(days=1)).isoformat(),
            )
        )

        fired = await poll_due_scheduled_tasks(settings.state_dir, get_session)

    assert fired == []
    assert fake_model.i == 0


async def test_a_failing_run_is_recorded_and_not_retried_every_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The schedule advances when a run starts, so a run whose session
    can't even be built is recorded as failed once -- not left due and
    re-attempted on every 30-second poll."""
    settings = _settings(tmp_path)
    store = ScheduledTriggerStore(settings.state_dir)
    store.save(
        _trigger(
            "trig-broken",
            schedule=ScheduleRule(kind="daily", at="09:00"),
            next_run_at=(datetime.now() - timedelta(minutes=1)).isoformat(),
        )
    )

    async def broken_get_session(thread_id: str) -> Any:
        raise RuntimeError("provider misconfigured")

    await poll_due_scheduled_tasks(settings.state_dir, broken_get_session)
    again = await poll_due_scheduled_tasks(settings.state_dir, broken_get_session)

    assert again == []
    resolved = store.load("trig-broken")
    assert resolved is not None
    [run] = resolved.runs
    assert run.status == "failed"
    assert run.error == "provider misconfigured"


# -- fire_trigger_now --


async def test_fire_trigger_now_runs_immediately_without_touching_the_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The detail page's "Run now" action must not disturb the trigger's
    regular schedule -- next_run_at and enabled stay exactly as they were,
    so running a daily 9am task by hand at 2pm doesn't make it skip
    tomorrow's real 9am fire."""
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="ran on demand")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        original_next_run = (datetime.now() + timedelta(hours=6)).isoformat()
        store.save(
            _trigger(
                "trig-7",
                schedule=ScheduleRule(kind="daily", at="09:00"),
                next_run_at=original_next_run,
                prompt="Do it now",
            )
        )

        run = await fire_trigger_now(settings.state_dir, "trig-7", get_session)

    assert fake_model.i == 1  # a real turn ran
    assert run is not None
    assert run.status == "completed"
    assert run.source == "manual"
    resolved = store.load("trig-7")
    assert resolved is not None
    # Untouched -- this is the whole point of run-now vs. a real poll fire.
    assert resolved.next_run_at == original_next_run
    assert resolved.enabled is True


async def test_fire_trigger_now_preserves_paused_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="ran while paused")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(_trigger("trig-8", enabled=False))

        run = await fire_trigger_now(settings.state_dir, "trig-8", get_session)

    resolved = store.load("trig-8")
    assert resolved is not None
    assert resolved.enabled is False  # run-now doesn't implicitly resume it
    assert run is not None and run.status == "completed"


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


# -- notes --


async def test_notes_are_in_the_run_prompt_and_the_run_can_update_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from langchain_core.messages import ToolCall

    settings = _settings(tmp_path)
    call = ToolCall(
        name="update_task_notes", args={"notes": "Processed through invoice 1043."}, id="n1"
    )
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(_trigger("trig-notes", prompt="Reconcile new invoices"))
        store.write_notes("trig-notes", "Processed through invoice 1000.")

        run = await asyncio.wait_for(
            fire_trigger_now(settings.state_dir, "trig-notes", get_session), timeout=5
        )

    assert run is not None and run.status == "completed"  # READ tier: never parks on approval
    [prompt] = _human_texts(fake_model.received[0])
    assert "Reconcile new invoices" in prompt
    assert "Processed through invoice 1000." in prompt
    assert "update_task_notes" in prompt
    assert store.read_notes("trig-notes") == "Processed through invoice 1043."


async def test_notes_disabled_leaves_them_out_of_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="done")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(_trigger("trig-nonotes", notes_enabled=False))
        store.write_notes("trig-nonotes", "stale secret")

        await fire_trigger_now(settings.state_dir, "trig-nonotes", get_session)

    [prompt] = _human_texts(fake_model.received[0])
    assert "stale secret" not in prompt
    assert "update_task_notes" not in prompt


def test_reconcile_interrupted_runs_marks_leftover_running_runs_failed(tmp_path: Path) -> None:
    store = ScheduledTriggerStore(tmp_path)
    store.save(_trigger("trig-left"))
    store.start_run("trig-left", "manual")

    assert reconcile_interrupted_runs(tmp_path) == 1
    resolved = store.load("trig-left")
    assert resolved is not None
    assert resolved.runs[0].status == "failed"
    assert reconcile_interrupted_runs(tmp_path) == 0


# -- approval_mode / model threading --


@pytest.mark.parametrize("approval_mode", ["auto", "skip"])
async def test_approval_mode_auto_or_skip_auto_approves_a_gated_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, approval_mode: str
) -> None:
    """A local file edit is covered by both tiers: unlike "manual" (where
    the call parks, awaiting an approval nobody is there to give), it is
    approved and executed."""
    from langchain_core.messages import ToolCall

    settings = _settings(tmp_path)
    call = ToolCall(name="write_file", args={"path": "note.txt", "content": "hi"}, id="call_1")
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(
            _trigger(
                "trig-10",
                prompt="Write hi to note.txt",
                approval_mode=approval_mode,
                notes_enabled=False,
            )
        )

        run = await asyncio.wait_for(
            fire_trigger_now(settings.state_dir, "trig-10", get_session), timeout=5
        )

    assert run is not None and run.status == "completed"
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"


async def test_approval_mode_manual_parks_the_run_as_needs_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody is there to approve a gated call during a run -- it must
    park durably (reported as needs_approval) rather than hang the poller
    or pretend it completed."""
    from langchain_core.messages import ToolCall

    settings = _settings(tmp_path)
    call = ToolCall(name="write_file", args={"path": "note.txt", "content": "hi"}, id="call_1")
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="", tool_calls=[call])])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(_trigger("trig-manual", prompt="Write hi to note.txt"))

        run = await asyncio.wait_for(
            fire_trigger_now(settings.state_dir, "trig-manual", get_session), timeout=5
        )

    assert run is not None and run.status == "needs_approval"
    assert not (tmp_path / "workspace" / "note.txt").exists()


async def test_approval_mode_auto_is_restored_after_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The tier belongs to the run: a person who keeps chatting in this
    thread afterwards gets ordinary approvals."""
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
        trigger = _trigger("trig-11", prompt="Write hi to note.txt", approval_mode="auto")
        run = ScheduledRun(
            run_id="r1",
            thread_id="reused-thread",
            started_at=datetime.now().isoformat(),
            source="manual",
        )

        await asyncio.wait_for(_run_in_session(trigger, run, "", session), timeout=5)
        assert session.accept_edits is False
        assert session.run_approval_mode is None


def _stub_script_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Runs scripts with the Python running pytest instead of provisioning
    the dedicated script venv."""
    import sys

    monkeypatch.setattr(
        "coscribe.tools.scripts.ensure_script_env", lambda state_dir: Path(sys.executable).parent
    )
    monkeypatch.setattr("coscribe.tools.scripts.venv_python", lambda venv_dir: Path(sys.executable))


def _script_call(call_id: str, script: str) -> Any:
    from langchain_core.messages import ToolCall

    return ToolCall(
        name="run_python_script", args={"script": script, "description": "test"}, id=call_id
    )


def _audit(settings: Settings) -> list[tuple[str, str, str]]:
    from coscribe.runtime_lg.audit import AuditLog

    return [(e.tool_name, e.decision, e.reason) for e in AuditLog(settings.state_dir).read_all()]


async def test_approval_mode_auto_approves_edits_but_parks_running_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The line between the two tiers: "auto" approves a local file edit,
    then parks before a script runs -- rather than rejecting it and letting
    the run carry on without it."""
    from langchain_core.messages import ToolCall

    _stub_script_env(monkeypatch)
    settings = _settings(tmp_path)
    write = ToolCall(name="write_file", args={"path": "note.txt", "content": "hi"}, id="call_1")
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[write]),
            AIMessage(content="", tool_calls=[_script_call("call_2", "print('ran')")]),
        ]
    )
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        ScheduledTriggerStore(settings.state_dir).save(
            _trigger("trig-auto", approval_mode="auto", notes_enabled=False)
        )
        run = await asyncio.wait_for(
            fire_trigger_now(settings.state_dir, "trig-auto", get_session), timeout=10
        )

    assert run is not None and run.status == "needs_approval"
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"
    assert _audit(settings) == [("write_file", "approve", "approval_mode_auto")]


async def test_approval_mode_skip_runs_code_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_script_env(monkeypatch)
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_script_call("call_1", "print('ran')")]),
            AIMessage(content="done"),
        ]
    )
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        ScheduledTriggerStore(settings.state_dir).save(
            _trigger("trig-skip", approval_mode="skip", notes_enabled=False)
        )
        run = await asyncio.wait_for(
            fire_trigger_now(settings.state_dir, "trig-skip", get_session), timeout=10
        )

    assert run is not None and run.status == "completed"
    assert _audit(settings) == [("run_python_script", "approve", "approval_mode_skip")]
    tool_result = fake_model.received[-1][-1]
    assert "ran" in str(tool_result.content)


async def test_approval_mode_skip_still_obeys_a_forbidding_exec_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Skipping approvals doesn't override the user's own standing rules."""
    import json

    _stub_script_env(monkeypatch)
    policy_path = tmp_path / "exec_policy.json"
    policy_path.write_text(
        json.dumps({"rules": [{"pattern": "shutil.rmtree", "decision": "forbidden"}]}),
        encoding="utf-8",
    )
    settings = _settings(tmp_path, exec_policy_path=policy_path)
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[_script_call("call_1", "import shutil; shutil.rmtree('x')")],
            ),
            AIMessage(content="done"),
        ]
    )
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        ScheduledTriggerStore(settings.state_dir).save(
            _trigger("trig-skip-policy", approval_mode="skip", notes_enabled=False)
        )
        run = await asyncio.wait_for(
            fire_trigger_now(settings.state_dir, "trig-skip-policy", get_session), timeout=10
        )

    assert run is not None and run.status == "completed"
    assert _audit(settings) == [("run_python_script", "reject", "exec_policy")]


async def test_finishing_a_parked_auto_run_keeps_its_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A person approves the script an "auto" run parked on; the file edit
    that follows is still approved automatically, not put to them too."""
    from langchain_core.messages import ToolCall

    _stub_script_env(monkeypatch)
    settings = _settings(tmp_path)
    write = ToolCall(name="write_file", args={"path": "note.txt", "content": "hi"}, id="call_2")
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_script_call("call_1", "print('ran')")]),
            AIMessage(content="", tool_calls=[write]),
            AIMessage(content="done"),
        ]
    )

    class _WatchingTab:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []

        async def send_json(self, data: dict[str, Any]) -> None:
            self.events.append(data)

    tab = _WatchingTab()
    sessions: list[Any] = []
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(_trigger("trig-resume", approval_mode="auto", notes_enabled=False))

        async def get_watched_session(thread_id: str) -> Any:
            session = await get_session(thread_id)
            session._live_websocket = tab
            sessions.append(session)
            return session

        run = await fire_trigger_now(settings.state_dir, "trig-resume", get_watched_session)
        for _ in range(100):
            if any(e["type"] == "approval_required" for e in tab.events):
                break
            await asyncio.sleep(0.02)
        approval = next(e for e in tab.events if e["type"] == "approval_required")
        sessions[0].resolve_approval(approval["id"], True)
        await asyncio.wait_for(sessions[0]._offer_task, timeout=10)

    assert run is not None and run.status == "needs_approval"
    assert approval["tool_name"] == "run_python_script"
    assert [e["type"] for e in tab.events].count("approval_required") == 1
    assert (tmp_path / "workspace" / "note.txt").read_text() == "hi"
    finished = store.load("trig-resume")
    assert finished is not None
    assert finished.runs[0].status == "completed"
    assert _audit(settings) == [
        ("run_python_script", "approve", "human"),
        ("write_file", "approve", "approval_mode_auto"),
    ]


async def test_trigger_model_override_switches_the_session_before_firing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="ran with override")])
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(_trigger("trig-12", model="fake:other-model"))

        captured: list[Any] = []

        async def get_session_and_capture(thread_id: str) -> Any:
            session = await get_session(thread_id)
            captured.append(session)
            return session

        run = await fire_trigger_now(settings.state_dir, "trig-12", get_session_and_capture)

    assert run is not None and run.status == "completed"
    assert captured[0]._model_string == "fake:other-model"


async def test_a_run_streams_live_to_a_tab_watching_its_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="all done")])

    class _WatchingTab:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []

        async def send_json(self, data: dict[str, Any]) -> None:
            self.events.append(data)

    tab = _WatchingTab()
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(_trigger("trig-live", notes_enabled=False))

        async def get_watched_session(thread_id: str) -> Any:
            session = await get_session(thread_id)
            session._live_websocket = tab
            return session

        await fire_trigger_now(settings.state_dir, "trig-live", get_watched_session)

    types = [event["type"] for event in tab.events]
    assert types[0] == "scheduled_run_started"
    assert tab.events[0]["text"].startswith('[Scheduled run of "Task trig-live"')
    assert {"type": "agent_message", "text": "all done"} in tab.events
    assert types[-1] == "tasks_changed"


async def test_history_shows_a_starting_runs_prompt_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tab can connect to a run just before or just after its prompt is
    checkpointed -- either way the history must show it exactly once."""
    settings = _settings(tmp_path)
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="done")])

    class _Tab:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []

        async def send_json(self, data: dict[str, Any]) -> None:
            self.events.append(data)

    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        session = await get_session(run_thread_id("trig-h", "r1"))
        prompt = '[Scheduled run of "T" · started manually · 2026-09-23 09:00. x]\n\nDo it'
        session.active_run_prompt = prompt

        before = _Tab()
        await session.send_history(before)
        await session.handle_user_message(prompt, _Tab())
        after = _Tab()
        await session.send_history(after)

    for tab in (before, after):
        [history] = tab.events
        assert [e["text"] for e in history["entries"] if e["kind"] == "user"] == [prompt]


async def test_a_run_parked_on_approval_offers_it_to_a_watching_tab(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from langchain_core.messages import ToolCall

    settings = _settings(tmp_path)
    call = ToolCall(name="write_file", args={"path": "note.txt", "content": "hi"}, id="call_1")
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="Skipped.")]
    )

    class _WatchingTab:
        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []

        async def send_json(self, data: dict[str, Any]) -> None:
            self.events.append(data)

    tab = _WatchingTab()
    sessions: list[Any] = []
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        get_session = await _make_get_session(settings, checkpointer, fake_model, monkeypatch)
        store = ScheduledTriggerStore(settings.state_dir)
        store.save(_trigger("trig-offer", prompt="Write hi to note.txt", notes_enabled=False))

        async def get_watched_session(thread_id: str) -> Any:
            session = await get_session(thread_id)
            session._live_websocket = tab
            sessions.append(session)
            return session

        run = await fire_trigger_now(settings.state_dir, "trig-offer", get_watched_session)
        # The offer runs in the background; let it deliver, then deny so
        # the pending approval future doesn't outlive the test.
        for _ in range(50):
            if any(e["type"] == "approval_required" for e in tab.events):
                break
            await asyncio.sleep(0.02)
        approval = next(e for e in tab.events if e["type"] == "approval_required")
        sessions[0].resolve_approval(approval["id"], False)
        await asyncio.wait_for(sessions[0]._offer_task, timeout=5)

    assert run is not None and run.status == "needs_approval"
    assert approval["tool_name"] == "write_file"
    # Resolved from the tab, the run finished -- its record must say so.
    resumed = store.load("trig-offer")
    assert resumed is not None
    assert resumed.find_run(run.run_id).status == "completed"

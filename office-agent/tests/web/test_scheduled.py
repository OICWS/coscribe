# ruff: noqa: E402
"""Web tests: scheduled tasks and workflows."""

import json
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
)

from coscribe.web.schemas import ScriptEnvPackageInstall

from ..test_web_routes import _flatten
from .helpers import (
    _NOTES_WORKFLOW,
    FakeToolCallingChatModel,
    _client_lg,
    _create_workflow_task,
    _receive_until,
    _run_turn,
    _structured,
    _tool_call,
    _wait_for_run_status,
)


def test_max_turns_ends_the_run_gracefully_instead_of_looping_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Settings.max_turns used to have no runtime_lg equivalent at all (see
    config.py's docstring / runtime_lg/README.md's "audit + delete old
    runtime" section) -- a model that keeps calling tools forever would
    never stop. Wired via ModelCallLimitMiddleware(run_limit=max_turns,
    exit_behavior="end"): with max_turns=2 and a model that always proposes
    a tool call, the model must only ever be invoked twice -- a third call
    would IndexError against this fake's two-item `responses` list, proving
    the cap didn't hold (pytest would report that error, not a clean
    assertion failure, but it would still fail the test either way).

    Also exercises a real bug this test caught along the way: the
    middleware's own injected "limit exceeded" message arrives through
    astream(stream_mode=["messages"]) as one complete AIMessage, not an
    AIMessageChunk (it's added directly by a before_model hook, never
    actually streamed from a model) -- _stream_turn originally only handled
    AIMessageChunk/ToolMessage, so this text was silently dropped and the
    turn ended with a blank "[no reply -- ...]" instead of telling the user
    why it stopped. Fixed by adding an AIMessage branch to _stream_turn."""
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[_tool_call(f"call_{i}", "list_files", {})])
            for i in range(2)
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, max_turns=2) as client:
        with client.websocket_connect("/ws/t_max_turns") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "loop forever"})
            messages = _receive_until(ws, "tasks_changed")

    assert not any(m["type"] == "error" for m in messages)
    agent_message = next(m for m in messages if m["type"] == "agent_message")
    assert agent_message["text"] == "Model call limits exceeded: turn limit (2/2)"


def test_wake_poll_loop_starts_and_cancels_cleanly_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural check for web/app.py's lifespan-owned background poll
    loop (see runtime_lg/selfwake.py's poll_due_wakes -- functional
    coverage of poll_due_wakes itself lives in test_selfwake_resume.py,
    driven directly without the FastAPI/WebSocket layer around it). Stubs
    poll_due_wakes to just count calls, and sets wake_poll_seconds to 0 so
    the loop iterates as fast as the event loop lets it during the brief
    window the client is open -- confirms the loop actually runs (not just
    that it doesn't crash), and that TestClient.__exit__ (which runs the
    lifespan's shutdown half, cancelling the loop task) completes without
    raising -- a leaked/never-cancelled task or an unhandled
    CancelledError would surface as a test failure here."""
    calls = 0

    async def _counting_poll_due_wakes(state_dir: Any, get_session: Any) -> list[Any]:
        nonlocal calls
        calls += 1
        return []

    monkeypatch.setattr("coscribe.web.app.poll_due_wakes", _counting_poll_due_wakes)
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model, wake_poll_seconds=0) as client:
        time.sleep(0.05)
        client.get("/api/tools")  # any request -- just keeps the client's event loop alive

    assert calls > 0


def test_wake_poll_loop_publishes_background_events_for_fired_wakes_and_triggers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The desktop shell's tray notification (office-agent-desktop) needs
    a channel outside any browser WebSocket to learn a background/
    scheduled run finished -- background_events.py's BackgroundEventBus,
    fed from _wake_poll_loop's own return values from poll_due_wakes/
    poll_due_scheduled_tasks (see that loop's own comments), and exposed
    as app.state.background_events for exactly this kind of test. Reaches
    it via client.portal (the same anyio BlockingPortal TestClient itself
    uses to run the app's event loop) rather than the real /internal/
    events SSE endpoint: that endpoint's response never completes by
    design (a persistent stream), and starlette's synchronous TestClient
    only ever returns a response once the ASGI app's call finishes --
    confirmed live, driving it through actual HTTP here just hangs
    forever. The endpoint itself is covered separately, structurally,
    below."""
    import types

    async def _fake_poll_due_wakes(state_dir: Any, get_session: Any) -> list[Any]:
        return [types.SimpleNamespace(reason="research done", thread_id="thread-a")]

    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore, create_trigger

    store = ScheduledTriggerStore(tmp_path / "state")
    digest = create_trigger(store, name="daily digest", kind="manual", at="", prompt="p")
    _, fired_run, _ = store.start_run(digest.trigger_id, "scheduled")
    store.finish_run(digest.trigger_id, fired_run.run_id, "failed", "no network")

    async def _fake_poll_due_scheduled_tasks(
        state_dir: Any, get_session: Any, on_pruned: Any = None
    ) -> list[Any]:
        return [store.load(digest.trigger_id)]

    monkeypatch.setattr("coscribe.web.app.poll_due_wakes", _fake_poll_due_wakes)
    monkeypatch.setattr("coscribe.web.app.poll_due_scheduled_tasks", _fake_poll_due_scheduled_tasks)
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model, wake_poll_seconds=0) as client:
        bus = client.app.state.background_events
        queue = client.portal.call(bus.subscribe)
        first = client.portal.call(queue.get)
        second = client.portal.call(queue.get)

    events = {(e["kind"], e["status"], e["title"], e["thread_id"]) for e in (first, second)}
    assert events == {
        ("wake", "completed", "research done", "thread-a"),
        ("scheduled_task", "failed", "daily digest", fired_run.thread_id),
    }


def test_script_env_package_install_does_not_block_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: this endpoint used
    to call install_package directly inside its `async def` handler, which
    runs the blocking subprocess.run wait *on the single asyncio event
    loop* -- freezing every other request this process serves for as long
    as pip takes, not just this one response. Live symptom: after setting
    a new interpreter override (which deletes the existing venv, see
    set_interpreter_override) and clicking Add on a package, the whole
    app looked dead -- Settings' other tabs came back empty, and a chat
    message sent over the WebSocket got no response at all.

    Proven here by making install_package artificially slow (a plain
    time.sleep, standing in for a real multi-minute pip subprocess) and
    confirming a concurrent, unrelated request still completes quickly
    instead of queuing behind it -- only possible if the slow call is
    actually offloaded to a thread (asyncio.to_thread) rather than
    awaited inline on the same loop. Uses client.portal (the anyio
    BlockingPortal TestClient itself runs the app's event loop through --
    see test_wake_poll_loop_publishes_background_events_for_fired_wakes_
    and_triggers' docstring for the same idiom) to genuinely run the slow
    call concurrently with a normal client.get, rather than TestClient's
    usual one-request-at-a-time synchronous dispatch."""

    def _slow_install(state_dir: Path, name: str) -> dict[str, object]:
        time.sleep(0.5)
        return {"success": True, "error": None}

    monkeypatch.setattr("coscribe.web.routes.settings.install_package", _slow_install)
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        route = next(
            r
            for r in _flatten(client.app.routes)
            if getattr(r, "path", None) == "/api/script-env/packages"
            and "POST" in (r.methods or set())
        )
        start = time.monotonic()
        slow_future = client.portal.start_task_soon(
            route.endpoint, ScriptEnvPackageInstall(package="six")
        )
        time.sleep(0.05)  # let the slow call actually start before racing it
        fast_response = client.get("/api/tools")
        fast_elapsed = time.monotonic() - start
        slow_result = slow_future.result(timeout=5)

    assert fast_response.status_code == 200
    assert fast_elapsed < 0.4  # well under the 0.5s sleep -- it wasn't queued behind it
    assert slow_result == {"success": True, "error": None}


def test_get_scheduled_tasks_endpoint_lists_saved_triggers_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTrigger, ScheduledTriggerStore, ScheduleRule

    store = ScheduledTriggerStore(tmp_path / "state")
    store.save(
        ScheduledTrigger(
            trigger_id="trig-1",
            name="Daily standup notes",
            schedule=ScheduleRule(kind="daily", at="09:00"),
            enabled=True,
            created_at="2026-01-01T00:00:00",
            next_run_at="2026-01-02T09:00:00",
            prompt="Summarize yesterday",
        )
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/scheduled-tasks")

    assert response.status_code == 200
    [trigger] = response.json()
    assert trigger["trigger_id"] == "trig-1"
    assert trigger["name"] == "Daily standup notes"


def test_run_now_returns_at_once_and_the_run_finishes_in_the_background_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="report written")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "Report", "kind": "manual", "at": "", "prompt": "Write the report"},
        ).json()
        response = client.post(f"/api/scheduled-tasks/{created['trigger_id']}/run")
        body = response.json()
        run = _wait_for_run_status(client, created["trigger_id"], body["run"]["run_id"])

        with client.websocket_connect(f"/ws/{body['run']['thread_id']}") as ws:
            state = ws.receive_json()
            history = ws.receive_json()

    assert response.status_code == 200
    assert body["run"]["status"] == "running"
    assert body["run"]["source"] == "manual"
    assert run["status"] == "completed"
    assert state["turn_in_flight"] is False
    assert history["entries"][0]["kind"] == "user"
    assert history["entries"][0]["text"].startswith('[Scheduled run of "Report"')
    assert history["entries"][-1] == {"kind": "agent", "text": "report written"}


def test_deleting_a_task_deletes_its_run_conversations_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="done")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "Temp", "kind": "manual", "at": "", "prompt": "Do it"},
        ).json()
        run = client.post(f"/api/scheduled-tasks/{created['trigger_id']}/run").json()["run"]
        _wait_for_run_status(client, created["trigger_id"], run["run_id"])

        assert client.delete(f"/api/scheduled-tasks/{created['trigger_id']}").status_code == 200
        with client.websocket_connect(f"/ws/{run['thread_id']}") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    assert history["entries"] == []


def test_startup_sweeps_run_conversations_no_task_lists_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A task deleted without a checkpointer at hand (the model's
    delete_scheduled_task) leaves its run conversations behind; the next
    startup removes them and keeps every run a task still lists."""
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="done"), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        runs = {}
        for name in ("Keep", "Drop"):
            created = client.post(
                "/api/scheduled-tasks",
                json={"name": name, "kind": "manual", "at": "", "prompt": "Do it"},
            ).json()
            run = client.post(f"/api/scheduled-tasks/{created['trigger_id']}/run").json()["run"]
            _wait_for_run_status(client, created["trigger_id"], run["run_id"])
            runs[name] = (created["trigger_id"], run["thread_id"])
    ScheduledTriggerStore(tmp_path / "state").delete(runs["Drop"][0])

    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        histories = {}
        for name, (_, thread_id) in runs.items():
            with client.websocket_connect(f"/ws/{thread_id}") as ws:
                ws.receive_json()  # state
                histories[name] = ws.receive_json()["entries"]

    assert histories["Keep"] != []
    assert histories["Drop"] == []


def test_scheduled_task_notes_endpoints_lg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coscribe.tools.scheduled_tasks import MAX_NOTES_CHARS

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "N", "kind": "manual", "at": "", "prompt": "p"},
        ).json()
        url = f"/api/scheduled-tasks/{created['trigger_id']}/notes"
        empty = client.get(url).json()
        saved = client.put(url, json={"notes": "  left off at page 3  "}).json()
        too_long = client.put(url, json={"notes": "x" * (MAX_NOTES_CHARS + 1)})
        missing = client.get("/api/scheduled-tasks/nope/notes")

    assert created["notes_enabled"] is True
    assert empty == {"notes": ""}
    assert saved == {"notes": "left off at page 3"}
    assert too_long.status_code == 400
    assert missing.status_code == 404


def test_create_scheduled_task_is_a_draft_the_user_reviews_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model's create_scheduled_task call never creates anything
    itself: the user gets the draft, saves (or dismisses) it from the UI,
    and their answer becomes the tool's result."""
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    draft_args = {"name": "Weekly export", "kind": "weekly", "at": "09:00", "prompt": "Export"}
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(
                content="", tool_calls=[_tool_call("d1", "create_scheduled_task", draft_args)]
            ),
            AIMessage(content="Saved it."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_draft") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "make this weekly"})
            draft = _receive_until(ws, "task_draft_required")[-1]
            ws.send_json(
                {
                    "type": "question_response",
                    "id": draft["id"],
                    "answer": 'Saved as scheduled task "Weekly export".',
                }
            )
            messages = _receive_until(ws, "tasks_changed")

    assert draft["draft"] == draft_args
    assert not any(m["type"] == "tool_result" for m in messages)
    tool_message = next(m for m in fake_model.received[-1] if isinstance(m, ToolMessage))
    assert tool_message.content == 'Saved as scheduled task "Weekly export".'
    assert ScheduledTriggerStore(tmp_path / "state").list_all() == []


def test_saveworkflow_asks_the_model_to_draft_a_task_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="drafting")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_saveworkflow") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/saveworkflow"})
            usage = ws.receive_json()
            ws.send_json({"type": "user_message", "text": "/saveworkflow Monthly close"})
            _receive_until(ws, "tasks_changed")

    assert usage == {"type": "error", "message": "Usage: /saveworkflow <name>"}
    [human] = [m for m in fake_model.received[-1] if isinstance(m, HumanMessage)]
    assert 'named "Monthly close"' in str(human.content)
    assert "create_scheduled_task" in str(human.content)


def test_create_scheduled_task_endpoint_persists_a_prompt_backed_trigger_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/scheduled-tasks",
            json={
                "name": "Daily standup notes",
                "kind": "daily",
                "at": "09:00",
                "prompt": "Summarize yesterday",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Daily standup notes"
    assert body["enabled"] is True
    resolved = ScheduledTriggerStore(tmp_path / "state").load(body["trigger_id"])
    assert resolved is not None
    assert resolved.prompt == "Summarize yesterday"


def test_create_scheduled_task_endpoint_rejects_invalid_payload_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/scheduled-tasks",
            json={"name": "x", "kind": "daily", "at": "09:00", "prompt": "  "},
        )

    assert response.status_code == 400
    assert "error" in response.json()


def test_pause_and_resume_scheduled_task_endpoints_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "x", "kind": "daily", "at": "09:00", "prompt": "p"},
        ).json()

        paused = client.post(f"/api/scheduled-tasks/{created['trigger_id']}/pause")
        assert paused.status_code == 200
        assert paused.json()["enabled"] is False

        resumed = client.post(f"/api/scheduled-tasks/{created['trigger_id']}/resume")
        assert resumed.status_code == 200
        assert resumed.json()["enabled"] is True

    assert ScheduledTriggerStore(tmp_path / "state").load(created["trigger_id"]) is not None


def test_pause_scheduled_task_endpoint_unknown_id_404s_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post("/api/scheduled-tasks/nope/pause")

    assert response.status_code == 404


def test_delete_scheduled_task_endpoint_lg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "x", "kind": "daily", "at": "09:00", "prompt": "p"},
        ).json()

        response = client.delete(f"/api/scheduled-tasks/{created['trigger_id']}")

    assert response.status_code == 200
    assert response.json() == {"deleted": created["trigger_id"]}
    assert ScheduledTriggerStore(tmp_path / "state").load(created["trigger_id"]) is None


def test_delete_scheduled_task_endpoint_unknown_id_404s_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.delete("/api/scheduled-tasks/nope")

    assert response.status_code == 404


def test_get_threads_endpoint_excludes_scheduled_task_threads_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fired Scheduled Task's own dedicated conversation (thread_id
    "scheduled-<trigger_id>", see tools/scheduled_tasks.py's
    SCHEDULED_THREAD_PREFIX) has its own surface in the frontend's
    Scheduled section -- it must not also show up in the ordinary chat
    sidebar's session list once its first turn writes a checkpoint,
    per an explicit request that the two stay separate."""
    # One response per turn -- two separate websocket connections below
    # each run their own turn against the same fake_model.
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="hi"), AIMessage(content="hi again")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_ordinary") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello"})
            _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/scheduled-abc123") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "do it"})
            _receive_until(ws, "tasks_changed")

        thread_ids = {t["thread_id"] for t in client.get("/api/threads").json()}

    assert "t_ordinary" in thread_ids
    assert "scheduled-abc123" not in thread_ids


def test_a_workflow_task_runs_waits_for_approval_and_finishes_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("one two three", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[_structured({"words": 3})])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        task = _create_workflow_task(client)
        assert task["workflow"]["steps"][0]["tool"] == "read_file"
        started = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={})
        run = started.json()["run"]
        parked = _wait_for_run_status(client, task["trigger_id"], run["run_id"])

        # Opening the run's thread like a conversation must leave it alone.
        with client.websocket_connect(f"/ws/{run['thread_id']}") as ws:
            ws.receive_json()  # state
            assert ws.receive_json()["entries"] == []

        answered = client.post(
            f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}/answer",
            json={"approved": True},
        )
        finished = _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        again = client.post(
            f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}/answer",
            json={"approved": True},
        )

    assert parked["status"] == "needs_approval"
    assert [(s["step_id"], s["status"]) for s in parked["steps"]] == [
        ("read", "done"),
        ("count", "done"),
        ("sane", "done"),
        ("ok", "waiting"),
    ]
    assert parked["steps"][1]["output"] == {"words": 3}
    assert parked["steps"][3]["output"] == "3 words"
    assert answered.status_code == 200
    assert finished["status"] == "completed" and finished["error"] is None
    assert [s["status"] for s in finished["steps"]] == ["done"] * 5
    assert (tmp_path / "workspace" / "out.md").read_text(encoding="utf-8") == "3 words"
    assert again.status_code == 409


def test_a_failed_workflow_step_can_be_retried_from_an_earlier_one_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("one two", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(
        responses=[_structured({"words": 0}), _structured({"words": 2})]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        task = _create_workflow_task(client)
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        failed = _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        client.post(
            f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}/retry",
            json={"step_id": "count"},
        )
        waiting = _wait_for_run_status(client, task["trigger_id"], run["run_id"])

    assert failed["status"] == "failed"
    assert failed["error"] == "Has words: 1 of 1 checks failed"
    assert failed["steps"][2]["checks"] == [{"held": False, "left": 0, "right": 0}]
    assert waiting["status"] == "needs_approval"
    assert [(s["step_id"], s["status"]) for s in waiting["steps"]][1:] == [
        ("count", "done"),
        ("sane", "done"),
        ("ok", "waiting"),
    ]
    assert waiting["steps"][1]["output"] == {"words": 2}


def test_a_failed_workflow_run_is_looked_into_in_a_new_conversation_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("one two", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="Counted."),
            _structured({"words": 0}),
            AIMessage(content="The count step read an empty file."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_source") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "统计 notes.txt 的字数"})
            _receive_until(ws, "tasks_changed")
        response = client.post(
            "/api/scheduled-tasks",
            json={"name": "Word count", "kind": "manual", "at": "", "workflow": _NOTES_WORKFLOW,
                  "from_thread": "t_source"},
        )  # fmt: skip
        task = response.json()
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        failed = _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        base = f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}"
        not_going = client.post(f"{base}/stop")
        started = client.post(f"{base}/investigate", json={})
        thread_id = started.json()["thread_id"]
        deadline = time.time() + 5
        while time.time() < deadline:
            threads = {t["thread_id"]: t for t in client.get("/api/threads").json()}
            if threads.get(thread_id, {}).get("message_count", 0) >= 2:
                break
            time.sleep(0.05)
        with client.websocket_connect(f"/ws/{thread_id}") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()["entries"]
        titles = {t["thread_id"]: t["preview"] for t in client.get("/api/threads").json()}

    assert task["source_thread"] == "t_source"
    assert failed["status"] == "failed"
    assert not_going.status_code == 409
    prompt = str(fake_model.received[2][-1].content)
    assert '[Look into a failed run of "Word count"' in prompt
    assert "Failed step: Has words (sane)" in prompt
    assert "What the user asked for when the task was made: 统计 notes.txt 的字数" in prompt
    assert history[0]["text"].startswith('[Look into a failed run of "Word count"')
    assert history[-1]["text"] == "The count step read an empty file."
    assert titles[thread_id] == "Look into: Word count"


def test_a_failed_run_of_a_task_set_to_is_looked_into_and_announced_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("one two", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(
        responses=[
            _structured({"words": 0}),
            AIMessage(
                content="", tool_calls=[_tool_call("c1", "test_workflow", {"task_id": "gone"})]
            ),
            AIMessage(content="The file was empty."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        task = _create_workflow_task(client)
        patched = client.patch(
            f"/api/scheduled-tasks/{task['trigger_id']}", json={"auto_investigate": True}
        ).json()
        bus = client.app.state.background_events
        queue = client.portal.call(bus.subscribe)
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        event = client.portal.call(queue.get)
        deadline = time.time() + 5
        while len(fake_model.received) < 3 and time.time() < deadline:
            time.sleep(0.05)
        failed = _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        # The model's third call starting doesn't mean its reply is saved
        # yet: read the history again until it is.
        history: list[dict[str, Any]] = []
        while time.time() < deadline + 5:
            with client.websocket_connect(f"/ws/{event['thread_id']}") as ws:
                # The conversation may still be finishing its reply, so live
                # events can come before the history does.
                messages = [ws.receive_json() for _ in range(2)]
                while not any(m.get("type") == "history" for m in messages):
                    messages.append(ws.receive_json())
                history = next(m for m in messages if m.get("type") == "history")["entries"]
            if any(e.get("text") == "The file was empty." for e in history):
                break
            time.sleep(0.1)

    assert patched["auto_investigate"] is True
    assert event["status"] == "failed" and event["title"] == "Word count"
    assert event["body"] == (
        "Failed -- Has words: 1 of 1 checks failed. coscribe is looking into it."
    )
    assert failed["investigation"] == event["thread_id"]
    assert '[Look into a failed run of "Word count"' in str(fake_model.received[1][-1].content)
    # Nobody is there to approve the reproduction, and it didn't wait.
    [tested] = [e for e in history if e.get("tool_name") == "test_workflow"]
    assert tested["result"] == {
        "status": "failed",
        "error": "There's no scheduled task with id 'gone'.",
    }
    assert any(e.get("text") == "The file was empty." for e in history)


def test_a_workflow_task_with_a_bad_reference_is_refused_with_the_step_named_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = json.loads(json.dumps(_NOTES_WORKFLOW))
    broken["steps"][2]["conditions"][0]["left"] = {"ref": "taly.words"}
    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        response = client.post(
            "/api/scheduled-tasks",
            json={"name": "Broken", "kind": "manual", "at": "", "workflow": broken},
        )

    assert response.status_code == 400
    assert "step 3 ('Has words') reads 'taly.words'" in response.json()["error"]


def test_a_workflow_run_thread_is_never_driven_as_a_conversation_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("a b", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[_structured({"words": 2})])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        task = _create_workflow_task(client)
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        with client.websocket_connect(f"/ws/{run['thread_id']}") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "hello?"})
            error = _receive_until(ws, "error")[-1]
        # The pending approval survived the connection, untouched.
        client.post(
            f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}/answer",
            json={"approved": True},
        )
        finished = _wait_for_run_status(client, task["trigger_id"], run["run_id"])

    assert error["message"] == "A workflow run doesn't take messages."
    assert fake_model.i == 1  # only the workflow's own model step
    assert finished["status"] == "completed"


def test_a_workflow_runs_activity_comes_from_its_steps_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "notes.txt").write_text("a b", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[_structured({"words": 2})])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        task = _create_workflow_task(client)
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        client.post(
            f"/api/scheduled-tasks/{task['trigger_id']}/runs/{run['run_id']}/answer",
            json={"approved": True},
        )
        _wait_for_run_status(client, task["trigger_id"], run["run_id"])
        activity = client.get(f"/api/threads/{run['thread_id']}/activity").json()

    assert [o["path"] for o in activity["outputs"]] == ["out.md"]
    assert {t["name"]: t["count"] for t in activity["tools"]} == {"read_file": 1, "write_file": 1}
    assert activity["tasks"] == []


def test_workflow_draft_distills_the_threads_tool_calls_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "input.txt").write_text("data", encoding="utf-8")
    draft = {
        "name": "Read the input",
        "workflow": {
            "inputs": [{"name": "path", "default": "input.txt"}],
            "steps": [
                {
                    "id": "read",
                    "kind": "tool",
                    "title": "Read it",
                    "tool": "read_file",
                    "args": {"path": "{{path}}"},
                    "save_as": "text",
                }
            ],
        },
        "notes": ["The path became an input."],
    }
    read = _tool_call("c1", "read_file", {"path": "input.txt"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[read]),
            AIMessage(content="done"),
            AIMessage(content=json.dumps(draft)),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        empty = client.post("/api/threads/t_empty/workflow-draft", json={})
        _run_turn(client, "t_draft", "read input.txt")
        response = client.post("/api/threads/t_draft/workflow-draft", json={"name": ""})

    assert empty.status_code == 400
    assert "hasn't used any tools" in empty.json()["error"]
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Read the input"
    assert body["notes"] == ["The path became an input."]
    assert body["workflow"]["steps"][0]["args"] == {"path": "{{path}}"}
    curator_request = str(fake_model.received[-1][-1].content)
    assert '[tool call #1] read_file({"path": "input.txt"})' in curator_request
    # Nothing is saved until the person reviews the draft.
    assert ScheduledTriggerStore(tmp_path / "state").list_all() == []


def test_validate_workflow_normalizes_or_explains_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="unused")])
    step = {"id": "a", "kind": "check", "title": "A", "conditions": []}
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        ok = client.post("/api/workflows/validate", json={"workflow": {"steps": []}})
        bad = client.post("/api/workflows/validate", json={"workflow": {"steps": [step]}})

    assert ok.json() == {"workflow": {"version": 1, "inputs": [], "steps": []}}
    assert bad.status_code == 400
    assert "step 1" in bad.json()["error"]


def _loop_workflow(skip: str) -> dict[str, Any]:
    return {
        "steps": [
            {"id": "ls", "kind": "tool", "title": "List", "tool": "list_files",
             "args": {"pattern": "*.txt"}, "save_as": "listing"},
            {"id": "each", "kind": "loop", "title": "Each file", "over": "listing.files",
             "item": "file", "collect": "text", "save_as": "texts",
             "steps": [
                 {"id": "read", "kind": "tool", "title": "Read", "tool": "read_file",
                  "args": {"path": "{{file}}"}, "save_as": "text"},
                 {"id": "not_b", "kind": "check", "title": "Not B",
                  "conditions": [{"left": {"ref": "file"}, "op": "ne", "right": {"value": skip}}]},
             ]},
            {"id": "keep", "kind": "tool", "title": "Keep", "tool": "write_file",
             "args": {"path": "out/all.md", "content": "All: {{texts}}", "overwrite": True}},
        ]
    }  # fmt: skip


def test_a_failed_pass_is_retried_where_it_stopped_not_the_whole_loop_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("a", "b", "c"):
        (workspace / f"{name}.txt").write_text(f"text {name}", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="unused")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        created = client.post(
            "/api/scheduled-tasks",
            json={"name": "Each", "kind": "manual", "at": "", "workflow": _loop_workflow("b.txt")},
        ).json()
        trigger_id = created["trigger_id"]
        run = client.post(f"/api/scheduled-tasks/{trigger_id}/run", json={}).json()["run"]
        failed = _wait_for_run_status(client, trigger_id, run["run_id"])

        fixed = {"name": "Each", "kind": "manual", "at": "", "workflow": _loop_workflow("z.txt")}
        assert client.put(f"/api/scheduled-tasks/{trigger_id}", json=fixed).status_code == 200
        client.post(f"/api/scheduled-tasks/{trigger_id}/runs/{run['run_id']}/retry", json={})
        done = _wait_for_run_status(client, trigger_id, run["run_id"])
        activity = client.get(f"/api/threads/{run['thread_id']}/activity").json()

    assert failed["status"] == "failed"
    assert failed["error"] == "Not B: 1 of 1 checks failed"
    stopped = {(s["step_id"], tuple(s["iteration"])): s["status"] for s in failed["steps"]}
    assert stopped[("not_b", (1,))] == "failed" and stopped[("each", ())] == "failed"
    assert done["status"] == "completed", done["error"]
    reads = [s for s in done["steps"] if s["step_id"] == "read"]
    assert [s["iteration"] for s in reads] == [[0], [1], [2]]
    # The first pass wasn't run again.
    first_read = next(s for s in failed["steps"] if s["step_id"] == "read")
    assert reads[0]["finished_at"] == first_read["finished_at"]
    assert (workspace / "out" / "all.md").read_text(encoding="utf-8").count("text") == 3
    assert {t["name"]: t["count"] for t in activity["tools"]} == {
        "list_files": 1, "read_file": 3, "write_file": 1,
    }  # fmt: skip
    assert [o["path"] for o in activity["outputs"]] == ["out/all.md"]


def test_the_chat_model_drafts_a_fixed_workflow_without_saving_it_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "input.txt").write_text("data", encoding="utf-8")
    draft = {
        "name": "Read the input",
        "workflow": {
            "steps": [{"id": "read", "kind": "tool", "title": "Read it", "tool": "read_file",
                       "args": {"path": "input.txt"}, "save_as": "text"}],
        },
        "notes": [],
    }  # fmt: skip
    read = _tool_call("c1", "read_file", {"path": "input.txt"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[read]),
            AIMessage(content="", tool_calls=[_tool_call("c2", "draft_workflow", {"name": ""})]),
            AIMessage(content=json.dumps(draft)),
            AIMessage(content="Drafted -- review it on the card."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_chat_draft") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "/accept-edits"})
            _receive_until(ws, "state")
            ws.send_json({"type": "user_message", "text": "read input.txt, then make it fixed"})
            streamed = _receive_until(ws, "tasks_changed")
        with client.websocket_connect("/ws/t_chat_draft") as ws:
            ws.receive_json()  # state
            history = ws.receive_json()

    [result] = [e for e in history["entries"] if e.get("tool_name") == "draft_workflow"]
    assert result["result"]["status"] == "drafted", result["result"]
    assert result["result"]["workflow"]["steps"][0]["tool"] == "read_file"
    assert result["result"]["workspace"] is None
    # The curator saw the read that worked, not the draft call itself.
    curator_request = str(fake_model.received[2][-1].content)
    assert '[tool call #1] read_file({"path": "input.txt"})' in curator_request
    assert "draft_workflow(" not in curator_request
    # The curator's own reply isn't the assistant talking.
    chat_text = "".join(
        str(m.get("text", "")) for m in streamed if m["type"] in ("agent_delta", "agent_message")
    )
    assert '"workflow"' not in chat_text
    assert "Drafted -- review it on the card." in chat_text
    assert ScheduledTriggerStore(tmp_path / "state").list_all() == []


def test_a_tasks_runs_work_in_its_own_workspace_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "elsewhere"
    folder.mkdir()
    (folder / "only-here.txt").write_text("found me", encoding="utf-8")
    workflow = {
        "steps": [{"id": "read", "kind": "tool", "title": "Read", "tool": "read_file",
                   "args": {"path": "only-here.txt"}, "save_as": "text"}],
    }  # fmt: skip
    fake_model = FakeToolCallingChatModel(responses=[AIMessage(content="unused")])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        missing = client.post(
            "/api/scheduled-tasks",
            json={"name": "R", "kind": "manual", "at": "", "workflow": workflow,
                  "workspace": str(tmp_path / "nowhere")},
        )  # fmt: skip
        task = client.post(
            "/api/scheduled-tasks",
            json={"name": "R", "kind": "manual", "at": "", "workflow": workflow,
                  "workspace": str(folder)},
        ).json()  # fmt: skip
        run = client.post(f"/api/scheduled-tasks/{task['trigger_id']}/run", json={}).json()["run"]
        done = _wait_for_run_status(client, task["trigger_id"], run["run_id"])

    assert missing.status_code == 400 and "isn't an existing folder" in missing.json()["error"]
    assert task["workspace"] == str(folder)
    assert done["status"] == "completed", done.get("error")
    assert done["steps"][0]["output"] == "found me"


def _saved_workflow_task(tmp_path: Path) -> Any:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore, create_trigger

    workflow = {
        "steps": [{"id": "read", "kind": "tool", "title": "Read it", "tool": "read_file",
                   "args": {"path": "input.txt"}, "save_as": "text"}],
    }  # fmt: skip
    return create_trigger(
        ScheduledTriggerStore(tmp_path / "state"),
        name="Read the input",
        kind="manual",
        at="",
        prompt="",
        workflow=workflow,
    )


def test_revising_a_saved_workflow_proposes_changes_without_saving_them_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools.scheduled_tasks import ScheduledTriggerStore

    task = _saved_workflow_task(tmp_path)
    revised = {
        "workflow": {
            "steps": [
                {"id": "read", "kind": "tool", "title": "Read it", "tool": "read_file",
                 "args": {"path": "input.txt"}, "save_as": "text"},
                {"id": "check", "kind": "check", "title": "Not empty",
                 "conditions": [{"left": {"ref": "text"}, "op": "not_empty"}]},
            ],
        },
        "changes": ["Stops the run when the file is empty"],
        "notes": [],
    }  # fmt: skip
    revise = _tool_call(
        "c1", "revise_workflow", {"task_id": task.trigger_id, "request": "stop if it's empty"}
    )
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[revise]),
            AIMessage(content=json.dumps(revised)),
            AIMessage(content="Review the changes on the card."),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        with client.websocket_connect("/ws/t_revise") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "make it stop if the file is empty"})
            messages = _receive_until(ws, "tasks_changed")

    [result] = [m for m in messages if m.get("tool_name") == "revise_workflow" and "result" in m]
    raw = result["result"]
    proposal = json.loads(raw) if isinstance(raw, str) else raw
    assert proposal["status"] == "drafted", proposal
    assert proposal["trigger_id"] == task.trigger_id
    assert proposal["changes"] == ["Stops the run when the file is empty"]
    assert [s["id"] for s in proposal["workflow"]["steps"]] == ["read", "check"]
    reviser_request = str(fake_model.received[1][-1].content)
    assert "stop if it's empty" in reviser_request
    assert '"id": "read"' in reviser_request
    saved = ScheduledTriggerStore(tmp_path / "state").load(task.trigger_id)
    assert saved is not None and len(saved.workflow["steps"]) == 1

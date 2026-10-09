"""What the Background tasks panel needs from a script task: it is listed, read,
stopped and forgotten like a sub-agent, and one that lost its supervisor in a
restart never shows as running."""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from coscribe.tools import background_tasks
from coscribe.tools.background_tasks import (
    BackgroundTask,
    BackgroundTaskStore,
    forget_finished_background_tasks,
    read_background_log,
    set_change_listener,
    stop_background_task,
)

from .test_web import FakeToolCallingChatModel, _client_lg


def _task(task_id: str, status: str = "succeeded", thread_id: str = "t1") -> BackgroundTask:
    return BackgroundTask(
        task_id=task_id,
        thread_id=thread_id,
        language="python",
        description=f"task {task_id}",
        status=status,
        started_at=datetime.now(UTC).isoformat(),
    )


def test_a_running_task_nobody_supervises_is_listed_as_interrupted(tmp_path: Path) -> None:
    store = BackgroundTaskStore(tmp_path)
    store.save(_task("lost", "running"))

    [task] = store.list_for_thread("t1")

    assert task.status == "interrupted" and task.finished_at is not None
    assert "restarted" in (task.note or "")
    assert store.load("lost").status == "interrupted"  # type: ignore[union-attr]


def test_a_running_task_this_process_supervises_stays_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = BackgroundTaskStore(tmp_path)
    live = _task("live", "running")
    store.save(live)
    monkeypatch.setitem(background_tasks._LIVE, "live", (live, object()))

    assert [t.status for t in store.list_for_thread("t1")] == ["running"]


def test_forgetting_removes_finished_tasks_and_their_logs_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = BackgroundTaskStore(tmp_path)
    live = _task("run", "running")
    for task in (_task("done"), _task("bad", "failed"), live, _task("other", thread_id="t2")):
        store.save(task)
    store.log_path("done").write_text("output")
    monkeypatch.setitem(background_tasks._LIVE, "run", (live, object()))

    removed = forget_finished_background_tasks(tmp_path, "t1")

    assert removed == 2
    assert not store.log_path("done").exists()
    assert {t.task_id for t in store.list_for_thread("t1")} == {"run"}
    assert [t.task_id for t in store.list_for_thread("t2")] == ["other"]


def test_the_log_is_the_end_of_the_output_and_an_id_cannot_leave_the_store(
    tmp_path: Path,
) -> None:
    store = BackgroundTaskStore(tmp_path)
    store.save(_task("done"))
    store.log_path("done").write_text("0123456789")
    (tmp_path / "secret.json").write_text("{}")

    log = read_background_log(tmp_path, "done", tail_bytes=4)

    assert log is not None and log["output"].endswith("6789")
    assert read_background_log(tmp_path, "missing") is None
    assert read_background_log(tmp_path, "../secret") is None
    assert store.load("..\\secret") is None


async def test_stopping_kills_the_process_and_the_panel_hears_of_both_ends(
    tmp_path: Path,
) -> None:
    store = BackgroundTaskStore(tmp_path)
    seen: list[tuple[str, str]] = []
    set_change_listener("t1", lambda task: seen.append((task.task_id, task.status)))
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "import time; time.sleep(60)",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    task = _task("sleeper", "running")
    store.save(task)
    background_tasks._LIVE["sleeper"] = (task, proc)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    supervisor = asyncio.create_task(background_tasks._supervise(store, task, proc, scratch, 60))

    with pytest.raises(KeyError):
        stop_background_task(tmp_path, "nope")
    stop_background_task(tmp_path, "sleeper")
    await asyncio.wait_for(supervisor, timeout=10)

    assert store.load("sleeper").status == "killed"  # type: ignore[union-attr]
    assert seen == [("sleeper", "killed")]
    with pytest.raises(ValueError, match="not running"):
        stop_background_task(tmp_path, "sleeper")


def test_the_panel_endpoints_list_read_stop_and_clear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        store = BackgroundTaskStore(tmp_path / "state")
        store.save(_task("done"))
        store.save(_task("lost", "running"))
        store.log_path("done").write_text("hello")

        listed = client.get("/api/threads/t1/background-tasks").json()
        log = client.get("/api/background-tasks/done/log").json()
        missing = client.get("/api/background-tasks/nope/log")
        escape = client.get("/api/background-tasks/..%5Cdone/log")
        stop_finished = client.post("/api/background-tasks/done/stop")
        stop_missing = client.post("/api/background-tasks/nope/stop")
        cleared = client.delete("/api/threads/t1/background-tasks").json()
        after = client.get("/api/threads/t1/background-tasks").json()

    assert {t["task_id"]: t["status"] for t in listed} == {
        "done": "succeeded",
        "lost": "interrupted",
    }
    assert log["output"] == "hello" and log["task"]["task_id"] == "done"
    assert missing.status_code == 404 and escape.status_code in (404, 422)
    assert stop_finished.status_code == 409 and stop_missing.status_code == 404
    assert cleared == {"removed": 2} and after == []

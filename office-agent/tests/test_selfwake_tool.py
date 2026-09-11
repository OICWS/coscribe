from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.background_tasks import BackgroundTask, BackgroundTaskStore
from coscribe.tools.selfwake import SignalStore, WakeStore, build_selfwake_tools
from coscribe.tools.workflows import WorkflowRun, WorkflowRunStore


def _tools_by_name(thread_id: str, state_dir: Path) -> dict[str, object]:
    return {tool.__name__: tool for tool in build_selfwake_tools(thread_id, state_dir)}  # type: ignore[attr-defined]


def _future_iso(seconds: int = 3600) -> str:
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _past_iso(seconds: int = 3600) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat()


# -- sleep_until / sleep_for --


def test_sleep_until_creates_a_pending_timer_wake(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)
    wake_at = _future_iso()

    result = tools["sleep_until"](wake_at=wake_at, reason="check the report")

    assert result["kind"] == "timer"
    assert result["status"] == "pending"
    assert result["reason"] == "check the report"
    wake = WakeStore(tmp_path).load(result["wake_id"])
    assert wake is not None
    assert wake.thread_id == "thread-1"


def test_sleep_until_normalizes_non_utc_offset_to_utc(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)
    future_plus5 = (datetime.now(UTC) + timedelta(hours=2)).astimezone(
        timezone(timedelta(hours=5))
    )
    wake_at = future_plus5.isoformat()
    assert not wake_at.endswith("+00:00")  # sanity: the input really is non-UTC

    result = tools["sleep_until"](wake_at=wake_at, reason="x")

    assert result["wake_at"].endswith("+00:00")
    assert datetime.fromisoformat(result["wake_at"]) == future_plus5


def test_sleep_until_rejects_past_timestamp(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    with pytest.raises(ValueError, match="must be in the future"):
        tools["sleep_until"](wake_at=_past_iso(), reason="x")


def test_sleep_until_rejects_unparseable_timestamp(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    with pytest.raises(ValueError, match="ISO-8601"):
        tools["sleep_until"](wake_at="not a date", reason="x")


def test_sleep_for_computes_wake_at_from_seconds(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)
    before = datetime.now(UTC)

    result = tools["sleep_for"](seconds=120, reason="check back soon")

    wake_at = datetime.fromisoformat(result["wake_at"])
    assert timedelta(seconds=110) < (wake_at - before) < timedelta(seconds=130)


def test_sleep_for_rejects_non_positive_seconds(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    with pytest.raises(ValueError, match="must be positive"):
        tools["sleep_for"](seconds=0, reason="x")


# -- wake_on --


def test_wake_on_requires_a_real_workflow_run(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    with pytest.raises(ValueError, match="No workflow run"):
        tools["wake_on"](job_id="does-not-exist", reason="x")


def test_wake_on_succeeds_for_a_real_run(tmp_path: Path) -> None:
    run_store = WorkflowRunStore(tmp_path)
    run_store.save(
        WorkflowRun(
            run_id="run-1",
            workflow_name="nightly-report",
            mode="chain",
            status="running",
            started_at=datetime.now(UTC).isoformat(),
        )
    )
    tools = _tools_by_name("thread-1", tmp_path)

    result = tools["wake_on"](job_id="run-1", reason="tell me when it's done")

    assert result["kind"] == "job"
    assert result["job_id"] == "run-1"


# -- wake_on_task --


def test_wake_on_task_requires_a_real_background_task(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    with pytest.raises(ValueError, match="No background task"):
        tools["wake_on_task"](task_id="does-not-exist", reason="x")


def test_wake_on_task_succeeds_for_a_real_task(tmp_path: Path) -> None:
    task_store = BackgroundTaskStore(tmp_path)
    task_store.save(
        BackgroundTask(
            task_id="task-1",
            thread_id="thread-1",
            language="python",
            description="crunch numbers",
            status="running",
            started_at=datetime.now(UTC).isoformat(),
        )
    )
    tools = _tools_by_name("thread-1", tmp_path)

    result = tools["wake_on_task"](task_id="task-1", reason="tell me when it's done")

    assert result["kind"] == "task"
    assert result["task_id"] == "task-1"


# -- wake_on_event / signal_event --


def test_wake_on_event_then_signal_event_round_trip(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)
    wake = tools["wake_on_event"](event_key="deploy-done", reason="tell the user")
    signal_store = SignalStore(tmp_path)

    assert signal_store.fired_since("deploy-done", wake["created_at"]) is False

    tools["signal_event"](event_key="deploy-done", payload="all green")

    assert signal_store.fired_since("deploy-done", wake["created_at"]) is True


def test_signal_event_before_wake_registered_does_not_retroactively_fire_it(
    tmp_path: Path,
) -> None:
    signal_store = SignalStore(tmp_path)
    signal_store.signal("deploy-done", "earlier signal")
    tools = _tools_by_name("thread-1", tmp_path)

    wake = tools["wake_on_event"](event_key="deploy-done", reason="x")

    assert signal_store.fired_since("deploy-done", wake["created_at"]) is False


# -- list_wakes / cancel_wake --


def test_list_wakes_is_scoped_to_its_own_thread(tmp_path: Path) -> None:
    thread_a = _tools_by_name("thread-a", tmp_path)
    thread_b = _tools_by_name("thread-b", tmp_path)
    thread_a["sleep_for"](seconds=60, reason="a's wake")
    thread_b["sleep_for"](seconds=60, reason="b's wake")

    assert [w["reason"] for w in thread_a["list_wakes"]()] == ["a's wake"]
    assert [w["reason"] for w in thread_b["list_wakes"]()] == ["b's wake"]


def test_cancel_wake_marks_it_cancelled_and_removes_it_from_list(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)
    wake = tools["sleep_for"](seconds=60, reason="x")

    cancelled = tools["cancel_wake"](wake_id=wake["wake_id"])

    assert cancelled["status"] == "cancelled"
    assert tools["list_wakes"]() == []


def test_cancel_wake_rejects_wake_from_another_thread(tmp_path: Path) -> None:
    thread_a = _tools_by_name("thread-a", tmp_path)
    thread_b = _tools_by_name("thread-b", tmp_path)
    wake = thread_a["sleep_for"](seconds=60, reason="x")

    with pytest.raises(KeyError):
        thread_b["cancel_wake"](wake_id=wake["wake_id"])


def test_cancel_wake_rejects_already_resolved_wake(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)
    wake = tools["sleep_for"](seconds=60, reason="x")
    tools["cancel_wake"](wake_id=wake["wake_id"])

    with pytest.raises(ValueError, match="already"):
        tools["cancel_wake"](wake_id=wake["wake_id"])


# -- risk classification --


def test_mutating_selfwake_tools_are_write_local_and_gated(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    for name in (
        "sleep_until",
        "sleep_for",
        "wake_on",
        "wake_on_task",
        "wake_on_event",
        "signal_event",
        "cancel_wake",
    ):
        metadata = get_tool_metadata(tools[name])
        assert metadata.risk_category == "WRITE_LOCAL"
        assert metadata.requires_approval is True


def test_list_wakes_is_read_and_ungated(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    metadata = get_tool_metadata(tools["list_wakes"])

    assert metadata.risk_category == "READ"
    assert metadata.requires_approval is False


# -- store-level persistence --


def test_wake_store_persists_and_resumes_across_instances(tmp_path: Path) -> None:
    first = _tools_by_name("thread-1", tmp_path)
    first["sleep_for"](seconds=60, reason="persisted")

    second = _tools_by_name("thread-1", tmp_path)

    assert [w["reason"] for w in second["list_wakes"]()] == ["persisted"]

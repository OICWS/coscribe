import concurrent.futures
import json
from pathlib import Path

import pytest

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.tasks import build_task_tools


def _tools_by_name(thread_id: str, state_dir: Path) -> dict[str, object]:
    return {tool.__name__: tool for tool in build_task_tools(thread_id, state_dir)}  # type: ignore[attr-defined]


def test_create_then_list(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    created = tools["task_create"](content="read config.py")

    assert created == {"id": "1", "content": "read config.py", "status": "pending"}
    assert tools["task_list"]() == [created]


def test_create_assigns_increasing_ids(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    first = tools["task_create"](content="first")
    second = tools["task_create"](content="second")

    assert (first["id"], second["id"]) == ("1", "2")


def test_update_changes_status(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)
    task = tools["task_create"](content="read config.py")

    updated = tools["task_update"](task_id=task["id"], status="in_progress")

    assert updated["status"] == "in_progress"
    assert tools["task_list"]()[0]["status"] == "in_progress"


def test_update_unknown_id_raises(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    with pytest.raises(KeyError):
        tools["task_update"](task_id="does-not-exist", status="completed")


def test_update_invalid_status_raises(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)
    task = tools["task_create"](content="read config.py")

    with pytest.raises(ValueError, match="status must be one of"):
        tools["task_update"](task_id=task["id"], status="done")


def test_all_task_tools_are_low_risk_no_approval(tmp_path: Path) -> None:
    tools = _tools_by_name("thread-1", tmp_path)

    for name in ("task_create", "task_update", "task_list"):
        metadata = get_tool_metadata(tools[name])
        assert metadata.risk_category == "READ"
        assert metadata.requires_approval is False


def test_tasks_persist_and_resume_across_toolkit_instances(tmp_path: Path) -> None:
    first_session = _tools_by_name("thread-1", tmp_path)
    first_session["task_create"](content="read config.py")

    second_session = _tools_by_name("thread-1", tmp_path)

    assert second_session["task_list"]() == [
        {"id": "1", "content": "read config.py", "status": "pending"}
    ]


def test_concurrent_task_create_calls_do_not_corrupt_the_state_file(tmp_path: Path) -> None:
    """Regression test for a real, live-hit bug: task_create is
    risk_category="READ" (deliberately ungated, see build_task_tools'
    docstring), so LangGraph dispatches several task_create calls from
    one AIMessage fully concurrently -- no approval-gate serialization
    slows them down the way it does for write_xlsx. TaskToolkit._save()
    used a *fixed* `.with_suffix(".tmp")` name for every save, so two
    threads racing os.replace() meant whichever lost found its own .tmp
    already consumed by the other's: "FileNotFoundError: [Errno 2] No
    such file or directory: '....tasks.tmp' -> '....tasks.json'". Fixed
    via tools/_file_locks.py's per-path lock in _save(); this drives 8
    concurrent task_create calls and asserts all 8 survive with valid
    JSON on disk."""
    tools = _tools_by_name("thread-1", tmp_path)

    def create(i: int) -> dict[str, object]:
        return tools["task_create"](content=f"task {i}")  # type: ignore[operator]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(create, range(8)))

    assert {r["id"] for r in results} == {str(i) for i in range(1, 9)}  # type: ignore[index]
    state_file = tmp_path / "thread-1.tasks.json"
    on_disk = json.loads(state_file.read_text(encoding="utf-8"))
    assert len(on_disk) == 8


def test_different_threads_have_independent_task_lists(tmp_path: Path) -> None:
    thread_a = _tools_by_name("thread-a", tmp_path)
    thread_b = _tools_by_name("thread-b", tmp_path)

    thread_a["task_create"](content="only in thread a")

    assert len(thread_a["task_list"]()) == 1
    assert thread_b["task_list"]() == []

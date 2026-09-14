import asyncio
import shutil
import socket
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.background_tasks import BackgroundTaskStore, build_background_task_tools


def _network_reachable() -> bool:
    """run_background_script's python path triggers ensure_script_env, the
    same one-time-venv-seeding cost tests/test_scripts_tool.py's identical
    helper skips offline for."""
    try:
        socket.create_connection(("pypi.org", 443), timeout=5).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _network_reachable(), reason="pypi.org not reachable from this environment"
)


@pytest.fixture(scope="module")
def shared_state_dir() -> Iterator[Path]:
    """Module-scoped for the same reason test_scripts_tool.py's identical
    fixture is: the underlying venv's one-time baseline-package install is
    a real ~20s network-bound cost, worth paying once per file, not once
    per test."""
    state_dir = Path(tempfile.mkdtemp(prefix="coscribe_bg_tasks_test_state_"))
    yield state_dir
    shutil.rmtree(state_dir, ignore_errors=True)


@dataclass
class BgTools:
    workspace: Path
    state_dir: Path
    thread_id: str
    run_background_script: Any
    check_background_task: Any
    list_background_tasks: Any
    kill_background_task: Any


def _make_tools(thread_id: str, workspace: Path, state_dir: Path) -> BgTools:
    by_name = {
        tool.__name__: tool for tool in build_background_task_tools(thread_id, workspace, state_dir)
    }
    return BgTools(
        workspace=workspace,
        state_dir=state_dir,
        thread_id=thread_id,
        run_background_script=by_name["run_background_script"],
        check_background_task=by_name["check_background_task"],
        list_background_tasks=by_name["list_background_tasks"],
        kill_background_task=by_name["kill_background_task"],
    )


@pytest.fixture
def tools(tmp_path: Path, shared_state_dir: Path) -> BgTools:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return _make_tools("thread-1", workspace, shared_state_dir)


async def _wait_until_finished(
    tools: BgTools, task_id: str, *, timeout: float = 20.0
) -> dict[str, Any]:
    """check_background_task until status leaves "running" -- the tool
    itself is deliberately non-blocking (that's the whole point), so a
    test asserting on a *finished* task's fields has to poll for it, same
    as a real caller using check_background_task instead of wake_on_task
    would."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        result = tools.check_background_task(task_id)
        if result["status"] != "running":
            return result
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"task {task_id!r} still running after {timeout}s: {result}")
        await asyncio.sleep(0.05)


async def test_run_background_script_returns_immediately_with_a_running_task_id(
    tools: BgTools,
) -> None:
    result = await tools.run_background_script(
        language="python", script="print('hello')", description="say hello"
    )

    assert result["status"] == "running"
    assert result["language"] == "python"
    assert result["description"] == "say hello"
    assert isinstance(result["task_id"], str) and result["task_id"]


async def test_check_background_task_reports_success_and_output_once_finished(
    tools: BgTools,
) -> None:
    started = await tools.run_background_script(
        language="python", script="print('hello from the background')", description="say hello"
    )

    finished = await _wait_until_finished(tools, started["task_id"])

    assert finished["status"] == "succeeded"
    assert finished["exit_code"] == 0
    assert "hello from the background" in finished["output"]


async def test_check_background_task_reports_failure_and_nonzero_exit_code(tools: BgTools) -> None:
    started = await tools.run_background_script(
        language="python", script="raise ValueError('boom')", description="error"
    )

    finished = await _wait_until_finished(tools, started["task_id"])

    assert finished["status"] == "failed"
    assert finished["exit_code"] == 1
    assert "ValueError: boom" in finished["output"]


async def test_check_background_task_can_read_partial_output_while_still_running(
    tools: BgTools,
) -> None:
    started = await tools.run_background_script(
        language="python",
        script=(
            "import sys, time\n"
            "print('first')\n"
            "sys.stdout.flush()\n"
            "time.sleep(2)\n"
            "print('second')\n"
        ),
        description="print, pause, print again",
    )

    # Give the process a moment to write and flush its first line, well
    # before its 2-second sleep is up.
    await asyncio.sleep(0.5)
    mid_run = tools.check_background_task(started["task_id"])

    assert mid_run["status"] == "running"
    assert "first" in mid_run["output"]
    assert "second" not in mid_run["output"]

    finished = await _wait_until_finished(tools, started["task_id"])
    assert "first" in finished["output"]
    assert "second" in finished["output"]


async def test_run_background_script_times_out_and_kills_a_runaway_script(tools: BgTools) -> None:
    started = await tools.run_background_script(
        language="python",
        script="import time\ntime.sleep(30)",
        description="sleep past the timeout",
        timeout_seconds=1,
    )

    finished = await _wait_until_finished(tools, started["task_id"])

    assert finished["status"] == "timed_out"


async def test_kill_background_task_marks_it_killed_not_failed(tools: BgTools) -> None:
    started = await tools.run_background_script(
        language="python",
        script="import time\ntime.sleep(30)",
        description="a long job",
    )

    killed = await tools.kill_background_task(started["task_id"])
    assert killed["status"] == "killed"

    # The real regression this guards against: _supervise seeing the
    # process actually exit (from being killed) and overwriting "killed"
    # with "failed" because the exit code looks like any other failure.
    finished = await _wait_until_finished(tools, started["task_id"])
    assert finished["status"] == "killed"


async def test_kill_background_task_rejects_an_already_finished_task(tools: BgTools) -> None:
    started = await tools.run_background_script(
        language="python", script="print('quick')", description="quick"
    )
    await _wait_until_finished(tools, started["task_id"])

    with pytest.raises(ValueError, match="not running"):
        await tools.kill_background_task(started["task_id"])


async def test_check_background_task_rejects_task_from_another_thread(
    tmp_path: Path, shared_state_dir: Path
) -> None:
    # Unique thread ids derived from tmp_path's own name (pytest already
    # guarantees that's unique per test) -- shared_state_dir is module-
    # scoped (see its own fixture docstring for why), so a fixed literal
    # like "thread-a" would collide with any other test in this file that
    # also happens to use that name, silently mixing their background
    # task records together in list_for_thread's shared on-disk glob.
    thread_a = _make_tools(f"{tmp_path.name}-a", tmp_path / "workspace", shared_state_dir)
    thread_b = _make_tools(f"{tmp_path.name}-b", tmp_path / "workspace", shared_state_dir)
    thread_a.workspace.mkdir()
    started = await thread_a.run_background_script(
        language="python", script="print(1)", description="a's task"
    )

    with pytest.raises(KeyError):
        thread_b.check_background_task(started["task_id"])


async def test_list_background_tasks_is_scoped_to_its_own_thread(
    tmp_path: Path, shared_state_dir: Path
) -> None:
    thread_a = _make_tools(f"{tmp_path.name}-a", tmp_path / "workspace", shared_state_dir)
    thread_b = _make_tools(f"{tmp_path.name}-b", tmp_path / "workspace", shared_state_dir)
    thread_a.workspace.mkdir()
    await thread_a.run_background_script(
        language="python", script="print(1)", description="a's task"
    )
    await thread_b.run_background_script(
        language="python", script="print(1)", description="b's task"
    )

    assert [t["description"] for t in thread_a.list_background_tasks()] == ["a's task"]
    assert [t["description"] for t in thread_b.list_background_tasks()] == ["b's task"]


async def test_run_background_script_does_not_pollute_the_workspace_with_the_script_file(
    tools: BgTools,
) -> None:
    started = await tools.run_background_script(
        language="python", script="print(1)", description="noop"
    )
    await _wait_until_finished(tools, started["task_id"])

    assert list(tools.workspace.iterdir()) == []


async def test_run_background_script_handles_chinese_text_in_the_script_and_its_output(
    tools: BgTools,
) -> None:
    """Regression test for the same real Windows charmap bug fixed in
    tools/scripts.py's run_python_script (see test_scripts_tool.py's
    identical test docstring) -- run_background_script has its own,
    independent copy of the write_text(script) call and its own subprocess
    environment, so it needed its own fix (explicit encoding="utf-8" on
    the write, PYTHONIOENCODING/PYTHONUTF8 forced for the python child)
    and its own regression test."""
    started = await tools.run_background_script(
        language="python",
        script="# 这是一个中文注释\nprint('你好，世界')",
        description="print a Chinese greeting",
    )
    finished = await _wait_until_finished(tools, started["task_id"])

    assert finished["status"] == "succeeded"
    assert finished["exit_code"] == 0
    assert "你好，世界" in finished["output"]


async def test_run_background_script_venv_is_isolated_from_the_coscribe_process(
    tools: BgTools,
) -> None:
    started = await tools.run_background_script(
        language="python",
        script="import sys\nprint(sys.executable)",
        description="report interpreter path",
    )
    finished = await _wait_until_finished(tools, started["task_id"])

    assert sys.executable not in finished["output"]
    assert "script-env" in finished["output"]


def test_run_background_script_is_exec_and_requires_approval(tools: BgTools) -> None:
    metadata = get_tool_metadata(tools.run_background_script)

    assert metadata.risk_category == "EXEC"
    assert metadata.requires_approval is True


def test_check_and_list_background_tasks_are_read_and_ungated(tools: BgTools) -> None:
    assert get_tool_metadata(tools.check_background_task).risk_category == "READ"
    assert get_tool_metadata(tools.check_background_task).requires_approval is False
    assert get_tool_metadata(tools.list_background_tasks).risk_category == "READ"
    assert get_tool_metadata(tools.list_background_tasks).requires_approval is False


def test_kill_background_task_is_write_local_and_gated(tools: BgTools) -> None:
    metadata = get_tool_metadata(tools.kill_background_task)

    assert metadata.risk_category == "WRITE_LOCAL"
    assert metadata.requires_approval is True


def test_background_task_store_round_trips_through_json() -> None:
    from coscribe.tools.background_tasks import BackgroundTask

    store = BackgroundTaskStore(Path(tempfile.mkdtemp(prefix="coscribe_bg_store_test_")))
    task = BackgroundTask(
        task_id="t1",
        thread_id="thread-1",
        language="node",
        description="a node task",
        status="succeeded",
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:00:05+00:00",
        exit_code=0,
        pid=1234,
    )

    store.save(task)
    loaded = store.load("t1")

    assert loaded == task
    shutil.rmtree(store.root, ignore_errors=True)


def test_background_task_store_tail_bounds_output_from_the_end() -> None:
    root = Path(tempfile.mkdtemp(prefix="coscribe_bg_store_test_"))
    store = BackgroundTaskStore(root)
    store.root.mkdir(parents=True)  # save() normally does this; writing the log directly here
    store.log_path("t1").write_text("0123456789")

    assert store.tail("t1", 4) == "[... 6 earlier bytes omitted ...]\n6789"
    assert store.tail("t1", 100) == "0123456789"
    assert store.tail("does-not-exist", 100) == ""
    shutil.rmtree(root, ignore_errors=True)

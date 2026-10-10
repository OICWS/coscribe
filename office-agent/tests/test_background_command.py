import asyncio
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from coscribe.tools.background_tasks import build_background_task_tools

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="uses sh syntax")


def _tools(tmp_path: Path, thread_id: str = "t1") -> dict[str, Any]:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    state = tmp_path / "state"
    return {
        tool.__name__: tool for tool in build_background_task_tools(thread_id, workspace, state)
    }


async def _finished(tools: dict[str, Any], task_id: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        result = tools["check_background_task"](task_id)
        if result["status"] != "running":
            return result
        assert asyncio.get_event_loop().time() < deadline, result
        await asyncio.sleep(0.05)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


async def test_a_command_returns_a_running_task_then_reports_its_output(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    started = await tools["run_background_command"](command="echo hello", description="greet")
    finished = await _finished(tools, started["task_id"])

    assert started["status"] == "running" and started["language"] == "shell"
    assert finished["status"] == "succeeded" and finished["exit_code"] == 0
    assert "hello" in finished["output"]


async def test_a_failing_command_reports_its_exit_code(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    started = await tools["run_background_command"](command="exit 3", description="fail")
    finished = await _finished(tools, started["task_id"])

    assert finished["status"] == "failed" and finished["exit_code"] == 3


async def test_a_command_runs_in_the_workspace_root(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    (tmp_path / "workspace" / "marker.txt").write_text("x")
    listing = "dir /b" if sys.platform == "win32" else "ls"

    started = await tools["run_background_command"](command=listing, description="list")
    finished = await _finished(tools, started["task_id"])

    assert "marker.txt" in finished["output"]


async def test_an_empty_command_is_refused(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    with pytest.raises(ValueError):
        await tools["run_background_command"](command="  ", description="nothing")


async def test_a_command_is_listed_only_in_its_own_conversation(tmp_path: Path) -> None:
    mine = _tools(tmp_path, "mine")
    theirs = _tools(tmp_path, "theirs")

    started = await mine["run_background_command"](command="echo hi", description="hi")
    await _finished(mine, started["task_id"])

    assert [t["task_id"] for t in mine["list_background_tasks"]()] == [started["task_id"]]
    assert theirs["list_background_tasks"]() == []


@posix_only
async def test_killing_a_command_stops_what_it_started_too(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    pid_file = tmp_path / "child.pid"
    # The shell forks a child and waits; killing only the shell would leave the child.
    command = f"sleep 60 & echo $! > {pid_file}; wait"

    started = await tools["run_background_command"](command=command, description="sleeper")
    for _ in range(100):
        if pid_file.exists() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.05)
    child = int(pid_file.read_text())
    assert _pid_alive(child)

    await tools["kill_background_task"](started["task_id"])
    finished = await _finished(tools, started["task_id"])
    for _ in range(100):
        if not _pid_alive(child):
            break
        await asyncio.sleep(0.05)

    assert finished["status"] == "killed"
    assert not _pid_alive(child)


@posix_only
async def test_a_command_past_its_timeout_is_killed_with_its_children(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    started = await tools["run_background_command"](
        command="sleep 60 & wait", description="too long", timeout_seconds=1
    )
    finished = await _finished(tools, started["task_id"])

    assert finished["status"] == "timed_out"

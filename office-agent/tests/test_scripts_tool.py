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
from coscribe.tools.scripts import build_script_tools


def _network_reachable() -> bool:
    """run_python_script's first call triggers ensure_script_env, which
    needs pypi.org to seed baseline packages -- skip cleanly offline,
    same reasoning as test_script_env.py's identical helper."""
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
    """Module-scoped so the underlying venv's one-time baseline-package
    install (a real ~20s network-bound cost) happens once for this whole
    file, not once per test -- every test here shares one script-env."""
    state_dir = Path(tempfile.mkdtemp(prefix="coscribe_scripts_test_state_"))
    yield state_dir
    shutil.rmtree(state_dir, ignore_errors=True)


@dataclass
class ScriptTools:
    workspace: Path
    run_python_script: Any


@pytest.fixture
def tools(tmp_path: Path, shared_state_dir: Path) -> ScriptTools:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    by_name = {tool.__name__: tool for tool in build_script_tools(workspace, shared_state_dir)}
    return ScriptTools(workspace=workspace, run_python_script=by_name["run_python_script"])


def test_run_python_script_returns_stdout_and_exit_code(tools: ScriptTools) -> None:
    result = tools.run_python_script(script="print('hello')", description="say hello")

    assert result["exit_code"] == 0
    assert result["stdout"] == "hello\n"
    assert result["stderr"] == ""
    assert result["timed_out"] is False


def test_run_python_script_runs_with_cwd_set_to_the_workspace(tools: ScriptTools) -> None:
    tools.run_python_script(
        script="from pathlib import Path\nPath('out.txt').write_text('hi')",
        description="write a file relative to cwd",
    )

    assert (tools.workspace / "out.txt").read_text() == "hi"


def test_run_python_script_surfaces_a_traceback_on_error(tools: ScriptTools) -> None:
    result = tools.run_python_script(script="raise ValueError('boom')", description="error")

    assert result["exit_code"] == 1
    assert "ValueError: boom" in result["stderr"]


def test_run_python_script_times_out_and_reports_partial_output(tools: ScriptTools) -> None:
    result = tools.run_python_script(
        script="import time\nprint('before')\nimport sys\nsys.stdout.flush()\ntime.sleep(5)",
        description="sleep past the timeout",
        timeout=1,
    )

    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert "before" in result["stdout"]
    assert "timed out after" in result["stderr"]


def test_run_python_script_does_not_pollute_the_workspace_with_the_script_file(
    tools: ScriptTools,
) -> None:
    tools.run_python_script(script="print(1)", description="noop")

    assert list(tools.workspace.iterdir()) == []


def test_run_python_script_is_exec_and_requires_approval(tools: ScriptTools) -> None:
    metadata = get_tool_metadata(tools.run_python_script)

    assert metadata.risk_category == "EXEC"
    assert metadata.requires_approval is True
    assert metadata.category == "scripts"


def test_run_python_script_venv_is_isolated_from_the_coscribe_process(
    tools: ScriptTools,
) -> None:
    """A script's own sys.executable must not be coscribe's own
    interpreter -- proves the isolation tools/script_env.py's docstring
    promises is real, not just a design intention."""
    result = tools.run_python_script(
        script="import sys\nprint(sys.executable)", description="report interpreter path"
    )

    assert result["stdout"].strip() != sys.executable
    assert "script-env" in result["stdout"]


def test_run_python_script_can_use_baseline_packages(tools: ScriptTools) -> None:
    result = tools.run_python_script(
        script="import pandas, openpyxl\nprint('ok')",
        description="import baseline packages",
    )

    assert result["exit_code"] == 0
    assert result["stdout"] == "ok\n"

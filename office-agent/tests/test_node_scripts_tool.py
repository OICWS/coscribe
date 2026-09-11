import shutil
import socket
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.node_scripts import build_node_script_tools


def _node_and_npm_available() -> bool:
    return shutil.which("node") is not None and shutil.which("npm") is not None


def _network_reachable() -> bool:
    """run_node_script's first call triggers ensure_node_env, which needs
    the npm registry to seed pptxgenjs -- skip cleanly offline, same
    reasoning as test_node_env.py's identical helper."""
    try:
        socket.create_connection(("registry.npmjs.org", 443), timeout=5).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _node_and_npm_available() or not _network_reachable(),
    reason="node/npm not installed, or registry.npmjs.org not reachable from this environment",
)


@pytest.fixture(scope="module")
def shared_state_dir() -> Iterator[Path]:
    """Module-scoped so the underlying node-env's one-time baseline-package
    install (a real network-bound cost) happens once for this whole file,
    not once per test -- every test here shares one node-env."""
    state_dir = Path(tempfile.mkdtemp(prefix="coscribe_node_scripts_test_state_"))
    yield state_dir
    shutil.rmtree(state_dir, ignore_errors=True)


@dataclass
class NodeScriptTools:
    workspace: Path
    run_node_script: Any


@pytest.fixture
def tools(tmp_path: Path, shared_state_dir: Path) -> NodeScriptTools:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    by_name = {tool.__name__: tool for tool in build_node_script_tools(workspace, shared_state_dir)}
    return NodeScriptTools(workspace=workspace, run_node_script=by_name["run_node_script"])


def test_run_node_script_returns_stdout_and_exit_code(tools: NodeScriptTools) -> None:
    result = tools.run_node_script(script="console.log('hello')", description="say hello")

    assert result["exit_code"] == 0
    assert result["stdout"] == "hello\n"
    assert result["stderr"] == ""
    assert result["timed_out"] is False


def test_run_node_script_runs_with_cwd_set_to_the_workspace(tools: NodeScriptTools) -> None:
    tools.run_node_script(
        script="require('fs').writeFileSync('out.txt', 'hi')",
        description="write a file relative to cwd",
    )

    assert (tools.workspace / "out.txt").read_text() == "hi"


def test_run_node_script_surfaces_an_error_on_failure(tools: NodeScriptTools) -> None:
    result = tools.run_node_script(script="throw new Error('boom')", description="error")

    assert result["exit_code"] == 1
    assert "boom" in result["stderr"]


def test_run_node_script_times_out_and_reports_partial_output(tools: NodeScriptTools) -> None:
    result = tools.run_node_script(
        script="console.log('before'); while (true) {}",
        description="hang past the timeout",
        timeout=1,
    )

    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert "before" in result["stdout"]
    assert "timed out after" in result["stderr"]


def test_run_node_script_does_not_pollute_the_workspace_with_the_script_file(
    tools: NodeScriptTools,
) -> None:
    tools.run_node_script(script="console.log(1)", description="noop")

    assert list(tools.workspace.iterdir()) == []


def test_run_node_script_is_exec_and_requires_approval(tools: NodeScriptTools) -> None:
    metadata = get_tool_metadata(tools.run_node_script)

    assert metadata.risk_category == "EXEC"
    assert metadata.requires_approval is True
    assert metadata.category == "scripts"


def test_run_node_script_can_use_pptxgenjs(tools: NodeScriptTools) -> None:
    """Regression test for the whole reason this tool exists -- confirms
    require("pptxgenjs") actually resolves via NODE_PATH despite the
    script file and cwd both living outside node-env's own directory, and
    that the deck it writes is a real, readable .pptx file."""
    script = """
const pptxgen = require("pptxgenjs");
const pres = new pptxgen();
pres.addSlide().addText("hello from run_node_script", { x: 0.5, y: 0.5 });
pres.writeFile({ fileName: "deck.pptx" }).then(() => console.log("done"));
"""
    result = tools.run_node_script(script=script, description="build a one-slide deck")

    assert result["exit_code"] == 0
    assert result["stdout"] == "done\n"
    deck_path = tools.workspace / "deck.pptx"
    assert deck_path.is_file()

    from pptx import Presentation

    presentation = Presentation(str(deck_path))
    assert len(presentation.slides) == 1

"""``run_node_script`` -- the Node.js sibling of ``tools/scripts.py``'s
``run_python_script``, added specifically so the model can write real
``pptxgenjs`` generation scripts (custom shapes, multi-column layouts,
precise positioning, icons) when ``write_pptx``'s fixed Title-and-Content/
Title-Only layouts can't express the slide being asked for -- the same
approach Anthropic's own pptx Skill uses.

Same safety posture as ``run_python_script``, restated here rather than
just cross-referenced since it's the whole justification for this tool's
existence: **no sandbox**, the approval prompt showing the full script
text before it runs is the entire safety mechanism, and deliberately no
import/library allowlist (trivially bypassable, same reasoning as Claude
Code's own Bash tool). The one thing genuinely different from
``run_python_script``: which packages the script can ``require()`` comes
from ``tools/node_env.py``'s ``state_dir/node-env`` directory via the
``NODE_PATH`` environment variable, not a baked-in interpreter path --
see that module's docstring for why Node has no venv equivalent and how
this was verified before being relied on here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..runtime.types import tool_metadata
from ._output_truncation import truncate_script_output
from .node_env import ensure_node_env

# Matches run_python_script's own timeout constants exactly -- same
# already-battle-tested balance (120s default, 600s/10min hard cap), no
# reason for arbitrary-code-execution scripts to behave differently by
# language.
_DEFAULT_TIMEOUT = 120.0
_MAX_TIMEOUT = 600.0


def _run_node_script(
    workspace_root: Path, state_dir: Path, script: str, timeout: float
) -> dict[str, object]:
    timeout = min(max(timeout, 1.0), _MAX_TIMEOUT)
    node_env_dir = ensure_node_env(state_dir)

    scratch_dir = Path(tempfile.mkdtemp(prefix="coscribe_node_script_"))
    script_path = scratch_dir / "script.js"
    script_path.write_text(script, encoding="utf-8")
    # Same Windows charmap bug as tools/scripts.py's _run_python_script --
    # see that function's comment for the full explanation. Node itself
    # always writes UTF-8 to a piped (non-TTY) stdout/stderr regardless of
    # the console codepage, so only the host-side write_text above (this
    # process writing script.js to disk) needs the explicit encoding here;
    # the subprocess.run encoding below still has to match on the read side.
    env = {**os.environ, "NODE_PATH": str(node_env_dir / "node_modules")}
    try:
        try:
            result = subprocess.run(
                # Resolved via shutil.which -- same Windows PATHEXT bug
                # runtime_lg/mcp.py's _to_lg_connection already documents;
                # `node` is a real .exe on Windows (not a .cmd like npm/
                # npx), but the same PATH-search gap still applies to
                # locating it via a bare name through CreateProcess.
                [shutil.which("node") or "node", str(script_path)],
                cwd=str(workspace_root),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            partial_stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout
            partial_stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr
            timeout_note = f"\n[timed out after {timeout}s]"
            return {
                "exit_code": None,
                "stdout": truncate_script_output(partial_stdout or ""),
                "stderr": truncate_script_output(partial_stderr or "") + timeout_note,
                "timed_out": True,
            }
        return {
            "exit_code": result.returncode,
            "stdout": truncate_script_output(result.stdout),
            "stderr": truncate_script_output(result.stderr),
            "timed_out": False,
        }
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def build_node_script_tools(
    workspace_root: str | Path, state_dir: str | Path
) -> list[Callable[..., Any]]:
    """Return the tool callables the Coordinator agent can call. `state_dir`
    is required for the same reason run_python_script's is -- the node-env
    directory this tool depends on has nowhere else to live."""
    root = Path(workspace_root)
    state = Path(state_dir)

    def run_node_script(
        script: str, description: str, timeout: float = _DEFAULT_TIMEOUT
    ) -> dict[str, object]:
        """Run a Node.js script and return its output -- reach for this,
        instead of write_pptx, when a slide needs something write_pptx's
        markdown-to-fixed-layout model can't express: custom shapes,
        multi-column layouts, precise positioning, icons. Like
        run_python_script, there is no sandbox around it (see this
        module's docstring): the script can read/write any file the
        coscribe process can reach and make any network call, not just
        things under the workspace. It runs with its working directory
        set to the workspace root, so relative paths in the script land
        there by default.

        Runs against a dedicated Node.js environment (not connected to
        this project's own frontend/ build) that already has pptxgenjs
        available via `require("pptxgenjs")` -- write a normal CommonJS
        script the way Anthropic's own pptx Skill does, then call
        `pres.writeFile({ fileName: "..." })` to save the deck under the
        workspace. Any other npm package the script needs (e.g. for
        rendering icons) must already be installed in that environment
        (the user manages this from the Environment settings tab) --
        `require()` a package that isn't there and the script will fail
        with the normal Node.js "Cannot find module" error; this tool
        cannot install packages on its own.

        `description` is a short, plain-language summary of what the
        script does (e.g. "Build a 3-slide deck comparing Q1-Q3 revenue
        with a two-column layout") -- shown alongside the script text
        wherever this call is presented for approval, so someone who
        doesn't read JavaScript can still tell what's about to run.

        Args:
            script: the complete Node.js (CommonJS) source to run
            description: one sentence, plain language, what this script does
            timeout: seconds to allow before killing the script (capped at 600)
        """
        return _run_node_script(root, state, script, timeout)

    return [
        tool_metadata(run_node_script, risk_category="EXEC", category="scripts"),
    ]

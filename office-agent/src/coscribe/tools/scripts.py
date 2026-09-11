"""``run_python_script`` -- one of two tools in this package classified
``risk_category="EXEC"`` (see ``tools/node_scripts.py`` for the other).

**No sandbox.** coscribe runs on the local desktop as a normal OS
process, with no OS-level sandbox (Seatbelt/bubblewrap/container/VM) around
it -- the same real-world posture Claude Code itself has on a native
Windows host with no WSL2 configured (its own docs: "This option does not
support native Windows. On Windows hosts, use WSL2 or one of the container
or VM approaches"). A script this tool runs can therefore read or write any
file the coscribe process's OS user can reach, and make any network
call -- there is no `WorkspaceScope`-style path check here, unlike every
other write tool in this codebase. **The approval prompt showing the full
script text before it runs is the entire safety mechanism**, not a defense
in depth on top of a real sandbox.

Deliberately no import/library allowlist either, for the same reason
Claude Code's own Bash tool doesn't restrict what commands can run: any such
restriction is trivially bypassable (``__import__``, ``importlib``,
``subprocess.run(["pip", "install", ...])``) and would only offer a false
sense of security while blocking legitimate use. The one thing this *does*
control -- deliberately, and for a different reason -- is which Python
environment the script runs in: ``tools/script_env.py``'s dedicated venv,
kept separate from coscribe's own runtime dependencies so a script
installing a package can't destabilize the agent itself. See that module's
docstring.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..runtime.types import tool_metadata
from .script_env import ensure_script_env, venv_python

# Matches Claude Code's own Bash tool exactly -- a default long enough for
# real data-processing work, capped short enough that an approved-but-
# runaway script can't hang a session indefinitely. Not guessed: this is
# the live, already-battle-tested balance that tool's own description
# documents (120s default, 600s/10min hard cap).
_DEFAULT_TIMEOUT = 120.0
_MAX_TIMEOUT = 600.0


def _run_python_script(
    workspace_root: Path, state_dir: Path, script: str, timeout: float
) -> dict[str, object]:
    timeout = min(max(timeout, 1.0), _MAX_TIMEOUT)
    venv_dir = ensure_script_env(state_dir)
    python = venv_python(venv_dir)

    scratch_dir = Path(tempfile.mkdtemp(prefix="coscribe_script_"))
    script_path = scratch_dir / "script.py"
    script_path.write_text(script)
    try:
        try:
            result = subprocess.run(
                [str(python), str(script_path)],
                cwd=str(workspace_root),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            partial_stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else exc.stdout
            partial_stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else exc.stderr
            return {
                "exit_code": None,
                "stdout": partial_stdout or "",
                "stderr": (partial_stderr or "") + f"\n[timed out after {timeout}s]",
                "timed_out": True,
            }
        return {
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": False,
        }
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def build_script_tools(
    workspace_root: str | Path, state_dir: str | Path
) -> list[Callable[..., Any]]:
    """Return the tool callables the Coordinator agent can call. `state_dir`
    is required (not optional like other tools' preview support) -- the
    script-env venv this tool depends on has nowhere else to live."""
    root = Path(workspace_root)
    state = Path(state_dir)

    def run_python_script(
        script: str, description: str, timeout: float = _DEFAULT_TIMEOUT
    ) -> dict[str, object]:
        """Run a Python script and return its output. The ONLY tool that
        executes arbitrary code -- there is no sandbox around it (see this
        module's docstring): the script can read/write any file the
        coscribe process can reach and make any network call, not just
        things under the workspace. It runs with its working directory set
        to the workspace root, so relative paths in the script land there
        by default, but nothing stops it from using an absolute path
        elsewhere.

        Runs against a dedicated Python environment (not the one
        coscribe itself runs on) that already has openpyxl, python-docx,
        python-pptx, pandas, and pdfplumber available -- the same libraries
        this package's own document/spreadsheet/presentation tools use, so
        a script can read/write .xlsx/.docx/.pptx files the same way they
        do, just with arbitrary Python logic (loops, pandas, multi-step
        calculations) instead of one fixed tool call. Reach for this when a
        task doesn't fit the built-in document/spreadsheet/presentation
        tools' shape -- e.g. processing thousands of rows, or a multi-step
        transformation those tools can't express in one call. Any other
        package the script needs must already be installed in that
        environment (the user manages this from the Environment settings
        tab) -- `import` a package that isn't there and the script will
        fail with the normal Python ModuleNotFoundError; this tool cannot
        install packages on its own.

        `description` is a short, plain-language summary of what the
        script does (e.g. "Sum the Revenue column in sales.xlsx and print
        the total") -- shown alongside the script text wherever this call
        is presented for approval, so someone who doesn't read Python can
        still tell what's about to run.

        Args:
            script: the complete Python source to run
            description: one sentence, plain language, what this script does
            timeout: seconds to allow before killing the script (capped at 600)
        """
        return _run_python_script(root, state, script, timeout)

    return [
        tool_metadata(run_python_script, risk_category="EXEC", category="scripts"),
    ]

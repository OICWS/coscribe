"""A dedicated Node.js/npm package directory for `run_node_script` to
execute pptxgenjs-based (and any other) scripts against -- the Node
sibling of `tools/script_env.py`'s Python venv, for the same reason:
`write_pptx`'s markdown-to-fixed-layout model can't express custom
shapes, multi-column layouts, or precise positioning the way a real
`pptxgenjs` generation script can (Anthropic's own pptx Skill is built
this way).

The isolation reasoning here is genuinely different from the Python
venv's, not just a port of the same two reasons: coscribe-the-backend has
zero Node.js runtime dependencies of its own (the `frontend/` build is a
separate, dev-time-only Node project), so there is no "don't destabilize
coscribe's own pinned deps" risk to guard against. The real reason to
keep installed packages (`pptxgenjs`, etc.) in their own directory
(`state_dir/node-env`) is simpler: so `npm install` here never touches
`frontend/`'s own unrelated `package.json`/`node_modules`. The
"inspectable, point-and-click list for the Environment settings tab"
reason still applies identically to the Python side.

Unlike a Python venv, there is no bundled/isolated Node *interpreter*
here -- Node has no equivalent of `venv`'s "bake a fixed interpreter+
site-packages path into one binary." Isolation is just `node_modules`
being local to this one directory; `run_node_script` (tools/
node_scripts.py) makes `require()` find packages installed here via the
`NODE_PATH` environment variable, verified empirically (a real throwaway
script resolving `require('pptxgenjs')` from a script file and `cwd` both
outside this directory) before writing that tool.

Node.js/npm are a genuinely optional system dependency here, unlike
Python (coscribe's own runtime, always present) -- see README.md's
"Optional: install Node.js" section. `ensure_node_env` checks for both
binaries explicitly and raises a clear, actionable RuntimeError naming
what's missing, rather than a raw FileNotFoundError from a failed
subprocess call.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

_NPM_TIMEOUT = 120.0
_SETUP_TIMEOUT = 300.0

# Just the one library, unlike the Python side's five-package baseline --
# pptxgenjs is the entire reason this exists; anything else a script needs
# (icon-rendering libraries, etc.) is on the model/user to add from the
# Environment settings tab, same "small baseline, add more as needed"
# symmetry the Python side already has.
_BASELINE_PACKAGES = ("pptxgenjs",)


def _require_node_and_npm() -> None:
    missing = [name for name in ("node", "npm") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(
            f"{' and '.join(missing)} not found on PATH -- install Node.js "
            "(https://nodejs.org) to use run_node_script."
        )


def _npm() -> str:
    # Resolved via shutil.which rather than passed through as a bare
    # "npm" -- same real, Windows-specific bug runtime_lg/mcp.py's
    # _to_lg_connection already documents and fixes for MCP connector
    # commands: on Windows, npm is a `.cmd` wrapper script, and the raw
    # CreateProcess call subprocess.run uses under the hood doesn't search
    # PATHEXT extensions the way a real command prompt does. Falls back to
    # the bare name if genuinely not found, so the resulting error is the
    # same informative one, not a different, more confusing failure.
    return shutil.which("npm") or "npm"


def ensure_node_env(state_dir: Path) -> Path:
    """Return the node-env directory, creating (and seeding with
    _BASELINE_PACKAGES) it on first use. Never raises for an environment
    that already exists and works; raises RuntimeError with the real
    failure otherwise -- same "don't swallow a seeding failure" philosophy
    script_env.py's ensure_script_env documents, for the same reason:
    run_node_script's own docstring promises pptxgenjs is there."""
    node_env_dir = state_dir / "node-env"
    if (node_env_dir / "node_modules" / "pptxgenjs").is_dir():
        return node_env_dir
    _require_node_and_npm()
    node_env_dir.mkdir(parents=True, exist_ok=True)
    # No `npm init` step needed -- `npm install` in a directory with no
    # package.json creates a minimal one itself (verified empirically).
    result = subprocess.run(
        [_npm(), "install", *_BASELINE_PACKAGES],
        cwd=str(node_env_dir),
        capture_output=True,
        text=True,
        timeout=_SETUP_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Could not install the default script packages: {result.stderr.strip()}"
        )
    return node_env_dir


def list_packages(state_dir: Path) -> list[dict[str, str]]:
    """Every top-level package installed in node-env, name+version, sorted
    -- `npm list --json --depth=0`'s own dependencies map, re-shaped to
    match script_env.py's list_packages return shape exactly."""
    node_env_dir = ensure_node_env(state_dir)
    result = subprocess.run(
        [_npm(), "list", "--json", "--depth=0"],
        cwd=str(node_env_dir),
        capture_output=True,
        text=True,
        timeout=_NPM_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not list installed packages: {result.stderr.strip()}")
    dependencies = json.loads(result.stdout).get("dependencies", {})
    packages = [
        {"name": name, "version": str(info.get("version", ""))}
        for name, info in dependencies.items()
    ]
    return sorted(packages, key=lambda pkg: pkg["name"].lower())


def install_package(
    state_dir: Path, package: str, timeout: float = _NPM_TIMEOUT
) -> dict[str, object]:
    """Install `package` (an npm package spec, e.g. "sharp" or
    "react-icons@5.0.0") into node-env. Never raises -- npm's own stderr
    (a typo'd package name, a real network failure) is exactly the useful,
    specific error to surface to whoever's adding this from the settings
    panel, so it comes back as `{"success": False, "error": ...}` rather
    than an exception, same contract as the Python side's install_package."""
    node_env_dir = ensure_node_env(state_dir)
    try:
        result = subprocess.run(
            [_npm(), "install", package],
            cwd=str(node_env_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Installing {package!r} timed out after {timeout}s"}
    if result.returncode != 0:
        return {"success": False, "error": result.stderr.strip() or result.stdout.strip()}
    return {"success": True, "error": None}


def uninstall_package(
    state_dir: Path, package: str, timeout: float = _NPM_TIMEOUT
) -> dict[str, object]:
    node_env_dir = ensure_node_env(state_dir)
    try:
        result = subprocess.run(
            [_npm(), "uninstall", package],
            cwd=str(node_env_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Removing {package!r} timed out after {timeout}s"}
    if result.returncode != 0:
        return {"success": False, "error": result.stderr.strip() or result.stdout.strip()}
    return {"success": True, "error": None}

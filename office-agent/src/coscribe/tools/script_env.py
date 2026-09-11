"""A dedicated, isolated Python virtual environment for `run_python_script`
to execute user-approved scripts in -- deliberately *not* the same
interpreter/site-packages coscribe's own process runs on.

Two reasons, both concrete: (1) a script installing a package (which it's
free to do -- see tools/scripts.py's docstring on why there's no import
allowlist) must never be able to collide with or destabilize coscribe's
own pinned runtime dependencies (langgraph/langchain and friends); this
project's own development sandbox has hit real, repeated dependency
breakage this session, so treating "packages a user script wants" and
"packages coscribe itself needs to run" as two separate installations
isn't theoretical caution. (2) it gives users an honest, inspectable place
("what's installed") to manage via the Environment settings tab, mirroring
Claude Code's own cloud-environment setup step -- but as a simple installed-
package list a non-technical user can point-and-click, not a freeform shell
script (see EnvironmentTab.tsx's docstring for why that UI choice was made
instead of copying Claude Code's textarea).

No sandboxing happens here or in tools/scripts.py -- this module only
isolates *dependencies*, not execution. A script run against this venv can
still read/write any file the coscribe process's OS user can, and make
any network call -- see tools/scripts.py's docstring for the full safety
model (approval + full script visibility is the only gate, same as Claude
Code's own Bash tool on a sandboxless host).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_VENV_TIMEOUT = 120.0
_SETUP_TIMEOUT = 300.0

# Pre-seeded on first use so a script can read/write the same file formats
# coscribe's own built-in tools do (openpyxl/python-docx/python-pptx/
# pdfplumber) plus pandas for bulk tabular work, without the user having to
# know to add these themselves first -- tools/scripts.py's own docstring
# promises this. Anything else the user wants is on them to add from the
# Environment settings tab.
_BASELINE_PACKAGES = ("openpyxl", "python-docx", "python-pptx", "pandas", "pdfplumber")


def venv_python(venv_dir: Path) -> Path:
    # venv's own bin/Scripts layout genuinely differs by platform (unlike
    # e.g. the soffice subprocess calls elsewhere, which don't need a
    # Windows branch because "soffice" resolves via PATH either way) --
    # a real, live-reported bug: a Windows user's run_python_script and
    # the Environment tab's package listing both raised
    # FileNotFoundError, since venv creates Scripts\python.exe on Windows,
    # not bin/python.
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def ensure_script_env(state_dir: Path) -> Path:
    """Return the script-env venv's directory, creating (and seeding with
    _BASELINE_PACKAGES) it on first use. Never raises for an environment
    that already exists and works; raises RuntimeError with the real
    failure if creation or seeding fails, since a caller (the
    run_python_script tool, or the settings API) can't do anything useful
    without a working venv and shouldn't guess why. Seeding failure is
    intentionally NOT swallowed into a "skipped" state the way e.g.
    write_pptx's optional LibreOffice QA is -- run_python_script's own
    docstring promises these packages are there, so silently proceeding
    without them would make that promise a lie the model has no way to
    detect until a script's `import pandas` fails confusingly later."""
    venv_dir = state_dir / "script-env"
    if venv_python(venv_dir).is_file():
        return venv_dir
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=_VENV_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not create the script environment: {result.stderr.strip()}")
    seed_result = subprocess.run(
        [str(venv_python(venv_dir)), "-m", "pip", "install", *_BASELINE_PACKAGES],
        capture_output=True,
        text=True,
        timeout=_SETUP_TIMEOUT,
    )
    if seed_result.returncode != 0:
        raise RuntimeError(
            f"Could not install the default script packages: {seed_result.stderr.strip()}"
        )
    return venv_dir


def list_packages(state_dir: Path) -> list[dict[str, str]]:
    """Every package installed in the script-env venv, name+version, sorted
    -- pip's own `--format=json` output re-shaped to that, `pip`/
    `setuptools`/`wheel` themselves excluded since they're venv scaffolding,
    not something a user asked for or would recognize adding."""
    venv_dir = ensure_script_env(state_dir)
    result = subprocess.run(
        [str(venv_python(venv_dir)), "-m", "pip", "list", "--format=json"],
        capture_output=True,
        text=True,
        timeout=_VENV_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Could not list installed packages: {result.stderr.strip()}")
    packages = json.loads(result.stdout)
    return sorted(
        (pkg for pkg in packages if pkg["name"].lower() not in ("pip", "setuptools", "wheel")),
        key=lambda pkg: pkg["name"].lower(),
    )


def install_package(
    state_dir: Path, package: str, timeout: float = _VENV_TIMEOUT
) -> dict[str, object]:
    """Install `package` (a pip requirement spec, e.g. "numpy" or
    "requests==2.31.0") into the script-env venv. Never raises -- pip's own
    stderr (a typo'd package name, a real network failure, a version that
    doesn't exist) is exactly the useful, specific error to surface to
    whoever's adding this from the settings panel, so it comes back as
    `{"success": False, "error": ...}` rather than an exception."""
    venv_dir = ensure_script_env(state_dir)
    try:
        result = subprocess.run(
            [str(venv_python(venv_dir)), "-m", "pip", "install", package],
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
    state_dir: Path, package: str, timeout: float = _VENV_TIMEOUT
) -> dict[str, object]:
    venv_dir = ensure_script_env(state_dir)
    try:
        result = subprocess.run(
            [str(venv_python(venv_dir)), "-m", "pip", "uninstall", "--yes", package],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"success": False, "error": f"Removing {package!r} timed out after {timeout}s"}
    if result.returncode != 0:
        return {"success": False, "error": result.stderr.strip() or result.stdout.strip()}
    return {"success": True, "error": None}

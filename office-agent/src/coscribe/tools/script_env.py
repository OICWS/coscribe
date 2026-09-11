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
import shutil
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

# A plain text file (not JSON -- one value, no reason for the ceremony)
# holding the user's manually-chosen interpreter path, when they've set
# one. Lives under state_dir alongside the venv itself, not the app's
# top-level .env (unlike e.g. COSCRIBE_BACKGROUND_ON_CLOSE) -- script_env.py
# already treats state_dir as its own self-contained world (see
# ensure_script_env's venv_dir), and this setting has nothing to do with
# the desktop shell the way that one does.
_INTERPRETER_OVERRIDE_FILE = "script_env_interpreter.txt"


def get_interpreter_override(state_dir: Path) -> str | None:
    """The user's manually-chosen interpreter path from the Environment
    settings tab, or None if they haven't set one (the normal case --
    auto-detection in _venv_create_candidates handles most machines)."""
    path = state_dir / _INTERPRETER_OVERRIDE_FILE
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def set_interpreter_override(state_dir: Path, python_path: str | None) -> dict[str, object]:
    """Persist (or, if `python_path` is falsy, clear) the user's manually-
    chosen interpreter. Exists because auto-detection has a real ceiling on
    Windows this project hit on real hardware: a GUI app's inherited PATH
    can be stale relative to what a freshly-installed Python actually
    registered (Explorer's own environment block doesn't refresh until
    logoff/logon), and the WindowsApps python.exe/python3.exe "app
    execution alias" stubs resolve via shutil.which without being real
    interpreters -- no amount of smarter auto-detection closes either gap.
    VS Code's Python extension hits the identical wall and solves it the
    same way: auto-detect as the default, plus an always-available manual
    "enter interpreter path" escape hatch.

    Validates by actually running `--version` -- same philosophy as
    install_package below (never raise, return the real, specific failure
    the settings panel can show verbatim) rather than trusting a path that
    merely exists on disk. On success, also deletes any existing script-env
    venv so the next ensure_script_env call rebuilds it with the newly
    chosen interpreter instead of silently keeping whatever was there
    before -- the whole point of picking one explicitly is to actually use
    it, not just to influence some future from-scratch install."""
    override_path = state_dir / _INTERPRETER_OVERRIDE_FILE
    if not python_path:
        override_path.unlink(missing_ok=True)
        return {"success": True, "error": None}
    try:
        result = subprocess.run(
            [python_path, "--version"], capture_output=True, text=True, timeout=10.0
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"success": False, "error": f"Could not run {python_path!r}: {exc}"}
    if result.returncode != 0:
        error = (result.stderr or result.stdout).strip() or f"exited with code {result.returncode}"
        return {"success": False, "error": error}
    state_dir.mkdir(parents=True, exist_ok=True)
    override_path.write_text(python_path, encoding="utf-8")
    shutil.rmtree(state_dir / "script-env", ignore_errors=True)
    return {"success": True, "error": None}


def _venv_create_candidates(state_dir: Path) -> list[str]:
    """Interpreters to try, in order, for bootstrapping the script-env
    venv. sys.executable first -- correct and sufficient in a normal dev
    install, where it's the real interpreter coscribe itself runs on. The
    rest are a fallback for when it isn't: a frozen desktop build makes
    sys.executable resolve to the app's own packaged executable rather
    than a real python.exe, so `subprocess.run([sys.executable, "-m",
    "venv", path])` doesn't reach venv's module runner at all -- it gets
    caught by *this app's own* `--host`/`--port` argparse (web/app.py's
    main()) as unrecognized arguments instead. Real, live-reported bug on
    a fresh Windows machine: "unrecognized arguments: -m venv
    <script-env path>". Deliberately not gated on `sys.frozen` (that flag
    is PyInstaller-specific -- e.g. Nuitka, which this project has also
    experimented with, uses a different one) -- trying sys.executable
    first and only falling back on an actual failure works regardless of
    which freezing tool ends up building the desktop exe, and costs
    nothing extra when it already works today (a bad `-m venv` call fails
    at this app's own argument parsing, instantly, before venv creation
    ever starts -- no wasted venv-timeout wait, no partial venv left
    behind to clean up).

    "py" tried before "python"/"python3" on Windows specifically: the
    official python.org installer always registers the "py" launcher on
    PATH (via C:\\Windows) even when the "Add python.exe to PATH"
    checkbox was left unchecked -- not every real end user's default
    choice -- so it's a genuinely more reliable find on Windows, not just
    a synonym for "python".

    A user-configured override (get_interpreter_override, set from the
    Environment settings tab) always goes first, ahead of even
    sys.executable -- once someone has explicitly picked an interpreter,
    auto-detection's guesses shouldn't outrank it, and this is also the
    escape hatch for machines where every auto-detected candidate is
    wrong (a stale-PATH GUI process, or a WindowsApps python.exe/
    python3.exe "app execution alias" stub that shutil.which finds but
    that isn't a real interpreter)."""
    auto_detected = [sys.executable, *fallbacks_for_platform()]
    override = get_interpreter_override(state_dir)
    if override:
        return [override, *[c for c in auto_detected if c != override]]
    return auto_detected


def fallbacks_for_platform() -> list[str]:
    """The auto-detected candidates below sys.executable, exposed on its
    own for the Environment tab's "detected" list in the interpreter API --
    kept separate from the override so the API can show what auto-
    detection alone would find, regardless of what's currently
    configured."""
    fallback_names = (
        ["py", "python3", "python"] if sys.platform == "win32" else ["python3", "python"]
    )
    return [found for found in (shutil.which(name) for name in fallback_names) if found]


def working_interpreters(candidates: list[str]) -> list[str]:
    """Filter `candidates` down to the ones that actually run as a Python
    interpreter -- for the Environment tab's "auto-detected" *display*
    list specifically, not for _venv_create_candidates' own try-in-order
    fallback chain (that one is deliberately left unfiltered: trying a bad
    candidate there costs nothing, it fails instantly and falls through).
    Showing one there is different -- it's offered to the user as a
    clickable "use this" chip, and on a packaged desktop build
    sys.executable is *always* wrong (the app's own frozen exe, not a
    real python.exe -- see _venv_create_candidates' docstring), so
    presenting it as "auto-detected" would be actively misleading rather
    than merely redundant. Real-hardware-reported: the interpreter picker
    listed the app's own coscribe-server.exe as an auto-detected
    candidate on a packaged build."""
    working = []
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, timeout=5.0
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            working.append(candidate)
    return working


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
    candidates = _venv_create_candidates(state_dir)
    result = subprocess.run(
        [candidates[0], "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
        timeout=_VENV_TIMEOUT,
    )
    for interpreter in candidates[1:]:
        if result.returncode == 0:
            break
        result = subprocess.run(
            [interpreter, "-m", "venv", str(venv_dir)],
            capture_output=True,
            text=True,
            timeout=_VENV_TIMEOUT,
        )
    if result.returncode != 0:
        raise RuntimeError(
            "Could not create the script environment (tried: "
            + ", ".join(candidates)
            + f"). Last error: {result.stderr.strip()}. If this machine has no "
            "system Python installed, install Python 3 from python.org -- the "
            '"Add python.exe to PATH" checkbox doesn\'t need to be checked, the '
            '"py" launcher it also installs is enough -- then try again.'
        )
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

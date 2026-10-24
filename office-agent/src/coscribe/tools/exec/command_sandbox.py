"""Commands (cmd) in Codex's Windows sandbox (decision 0022).

`codex sandbox` runs a command as a local sandbox user that Codex creates once
(`codex sandbox setup --elevated`, which needs an administrator). That user may write only the
conversation's folders; network is off unless the environment's is Full. It reads what Codex
grants its users, which is most of the disk and of the person's folders (Documents among them),
so an online command still goes to the person (`conversation/approvals.py`). Settings ›
Capabilities sets the sandbox up and records here which Codex did it; this module reads that
record and builds the command line, so the tools need nothing from the code module. Elsewhere,
or until it is set up, a command runs as the person."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_RECORD = "command-sandbox.json"
# Codex reads permission profiles from config.toml only; each set of folders gets its own,
# named after them, so commands running at once in different environments never share one.
_PROFILES_KEPT = 64
_DEFAULT = "coscribe-default"
# A command's batch file outlives its start only so a slow sandbox can read it.
_COMMAND_FILES_KEPT_SECONDS = 3600
_lock = threading.Lock()


def supported() -> bool:
    return sys.platform == "win32"


def sandbox_home(state_dir: Path) -> Path:
    """The sandbox's own Codex settings, apart from the code module's and the person's."""
    return Path(state_dir) / "command-sandbox" / "codex-home"


def _record_path(state_dir: Path) -> Path:
    return Path(state_dir) / _RECORD


def _read_record(state_dir: Path) -> dict[str, Any]:
    try:
        raw = json.loads(_record_path(state_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def record_sandbox_setup(state_dir: Path, codex: Path | None, error: str = "") -> None:
    """What Settings › Capabilities found when it set the sandbox up (or removed it: None)."""
    path = _record_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"codex": str(codex) if codex else "", "error": error}
    path.write_text(json.dumps(record), encoding="utf-8")


def sandbox_codex(state_dir: Path) -> Path | None:
    """The Codex that set the sandbox up, while it is set up and still there."""
    if not supported():
        return None
    codex = str(_read_record(state_dir).get("codex") or "")
    return Path(codex) if codex and Path(codex).is_file() else None


def sandbox_status(state_dir: Path) -> dict[str, str]:
    """ "ready", "off" (never set up, or removed), "failed" (with why) or "unsupported"."""
    if not supported():
        return {"state": "unsupported", "detail": "The command sandbox needs Windows."}
    if sandbox_codex(state_dir) is not None:
        return {"state": "ready", "detail": ""}
    error = str(_read_record(state_dir).get("error") or "")
    return {"state": "failed", "detail": error} if error else {"state": "off", "detail": ""}


def _toml_key(path: str) -> str:
    return json.dumps(path.replace("\\", "/"))


def _section(name: str, profile: dict[str, Any]) -> str:
    # The elevated sandbox needs ":root" readable.
    lines = [f"[permissions.{name}.filesystem]", '":root" = "read"']
    lines += [f'{_toml_key(p)} = "read"' for p in profile["readable"]]
    lines += [f'{_toml_key(p)} = "write"' for p in profile["writable"]]
    lines += ["", f"[permissions.{name}.network]", f"enabled = {json.dumps(profile['network'])}"]
    return "\n".join(lines) + "\n"


def _read_profiles(home: Path) -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads((home / "profiles.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _ensure_profile(home: Path, profile: dict[str, Any]) -> str:
    """The name of a profile with exactly these folders and network in `home`'s config.toml,
    added if new."""
    name = "coscribe-" + hashlib.sha256(json.dumps(profile).encode()).hexdigest()[:16]
    with _lock:
        profiles = _read_profiles(home)
        if name in profiles:
            return name
        profiles = {**dict(list(profiles.items())[-(_PROFILES_KEPT - 1) :]), name: profile}
        home.mkdir(parents=True, exist_ok=True)
        (home / "profiles.json").write_text(json.dumps(profiles), encoding="utf-8")
        # Codex refuses a config with profiles and no default (`codex sandbox uninstall` among
        # others); the default may read and write nothing it isn't given.
        none = {"readable": [], "writable": [], "network": False}
        config = f'default_permissions = "{_DEFAULT}"\n\n[windows]\nsandbox = "elevated"\n\n'
        config += "\n".join(
            _section(key, value) for key, value in {_DEFAULT: none, **profiles}.items()
        )
        tmp = home / "config.toml.tmp"
        tmp.write_text(config, encoding="utf-8")
        os.replace(tmp, home / "config.toml")
    return name


def sandboxed_command(
    state_dir: Path,
    command: str,
    root: Path,
    writable: Sequence[Path],
    readable: Sequence[Path],
    network: bool,
) -> tuple[list[str], dict[str, str]] | None:
    """The argv and environment that run `command` through cmd in the sandbox, in `root`, able
    to write only `writable`; None while the sandbox is not set up."""
    codex = sandbox_codex(state_dir)
    if codex is None:
        return None
    home = sandbox_home(state_dir)
    script = _command_file(state_dir, command)
    folders = {
        "writable": [str(p) for p in writable],
        "readable": [*(str(p) for p in readable), str(script.parent)],
        "network": network,
    }
    profile = _ensure_profile(home, folders)
    argv = [str(codex), "sandbox", "--permission-profile", profile, "-C", str(root), "--"]
    argv += ["cmd.exe", "/d", "/c", str(script)]
    return argv, {"CODEX_HOME": str(home)}


def _command_file(state_dir: Path, command: str) -> Path:
    """`command` as a batch file. Codex quotes each argument for CommandLineToArgvW, so a quote
    in a command line passed as one argument reaches cmd as \\", which cmd doesn't read; a batch
    file keeps the line as typed, %VARIABLES% included. UTF-8 with chcp 65001 keeps non-ASCII
    paths."""
    folder = Path(state_dir) / "command-sandbox" / "commands"
    folder.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - _COMMAND_FILES_KEPT_SECONDS
    for old in folder.glob("*.cmd"):
        with contextlib.suppress(OSError):
            if old.stat().st_mtime < cutoff:
                old.unlink()
    script = folder / f"{uuid.uuid4().hex}.cmd"
    script.write_text(
        f"@echo off\r\nchcp 65001 >nul\r\n{command}\r\n", encoding="utf-8", newline=""
    )
    return script


def warm_up_sandbox(state_dir: Path) -> None:
    """Runs one empty command: the first command after setup is refused ("Access is denied")
    while Codex finishes granting its users (seen on the Windows runner), so it is not the
    person's."""
    commands = Path(state_dir) / "command-sandbox" / "commands"
    sandboxed = sandboxed_command(state_dir, "rem", commands, [], [], False)
    if sandboxed is None:
        return
    argv, env = sandboxed
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            argv, env={**os.environ, **env}, capture_output=True, timeout=120, check=False
        )


def sandbox_setup_argv(codex: Path) -> list[str]:
    """Creates the sandbox users once; Windows asks an administrator to approve it."""
    return [str(codex), "sandbox", "setup", "--elevated", "--current-user"]


def sandbox_uninstall_argv(codex: Path) -> list[str]:
    return [str(codex), "sandbox", "uninstall"]

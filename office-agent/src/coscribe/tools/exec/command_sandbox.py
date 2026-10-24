"""Commands (cmd and PowerShell) in Codex's Windows sandbox (decision 0022).

`codex sandbox` runs a command as a local sandbox user that Codex creates once
(`codex sandbox setup --elevated`, which needs an administrator). That user may write only the
conversation's folders and reads the rest of the disk but not the person's profile; network is
off unless the environment allows it. Settings › Capabilities sets the sandbox up and records
here which Codex did it; this module reads that record and builds the command line, so the tools
need nothing from the code module. Elsewhere, or until it is set up, a command runs as the person.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

_RECORD = "command-sandbox.json"
# Codex reads permission profiles from config.toml only; each set of folders gets its own,
# named after them, so commands running at once in different environments never share one.
_PROFILES_KEPT = 64
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
    # The elevated sandbox needs ":root" readable; the person's profile stays unreadable all the
    # same, since the sandbox user is not the person.
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
        config = '[windows]\nsandbox = "elevated"\n\n'
        config += "\n".join(_section(key, value) for key, value in profiles.items())
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
    folders = {
        "writable": [str(p) for p in writable],
        "readable": [str(p) for p in readable],
        "network": network,
    }
    profile = _ensure_profile(home, folders)
    argv = [str(codex), "sandbox", "--permission-profile", profile, "-C", str(root), "--"]
    argv += ["cmd.exe", "/d", "/s", "/c", command]
    return argv, {"CODEX_HOME": str(home)}


def sandbox_setup_argv(codex: Path) -> list[str]:
    """Creates the sandbox users once; Windows asks an administrator to approve it."""
    return [str(codex), "sandbox", "setup", "--elevated", "--current-user"]


def sandbox_uninstall_argv(codex: Path) -> list[str]:
    return [str(codex), "sandbox", "uninstall"]

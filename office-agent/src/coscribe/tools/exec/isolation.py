"""What a script may reach, held by Windows instead of by the script (issue #560).

A run is started in one AppContainer (`_appcontainer.py`). Each folder it may use gets an ACE for
a capability derived from the folder's path, written once, when a run first needs it, and kept:
later runs only hold the capability. A run of one conversation holds only its own folders'
capabilities, so another conversation's folder stays closed to it even though the ACE is there.

Rights, per folder:
- read: read and list (`RX`).
- write: read plus create, overwrite and append (`RX,W`), without DELETE. Removing, renaming and
  replace-by-rename are refused by Windows, so a delete can only go through a trusted tool.

Every grant is recorded in `folder-grants.json` before `icacls` writes it, so a crash between the
two leaves an entry that `revoke` still removes. Volumes without ACLs (FAT, exFAT) cannot be
closed this way; SMB shares are not reachable from a container at all.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Literal

from ...runtime import NetworkGuard
from . import _appcontainer
from ._appcontainer import NETWORK_CAPABILITIES, ContainerProcess, ContainerUnavailable
from .script_env import ensure_script_env, venv_python

Access = Literal["read", "write"]

_RIGHTS: dict[Access, str] = {"read": "RX", "write": "RX,W"}
_REGISTRY = "folder-grants.json"
_SELF_TEST = "isolation-self-test.json"
_lock = threading.Lock()


@dataclass(frozen=True)
class Isolation:
    """The folders and the network one run may use. `runtime` is what the interpreter needs to
    start (the venv, its base installation, the npm directory): read only, like `readable`."""

    state_dir: Path
    writable: tuple[Path, ...] = ()
    readable: tuple[Path, ...] = ()
    runtime: tuple[Path, ...] = ()
    network: bool = True


def available() -> bool:
    return sys.platform == "win32"


def capability_name(path: Path, access: Access) -> str:
    key = os.path.normcase(str(path.resolve()))
    return f"coscribe.folder.{hashlib.sha256(key.encode('utf-8')).hexdigest()[:32]}.{access}"


def _folder_id(path: Path) -> float | None:
    """Tells a folder from one deleted and made again under the same name, which has lost the
    ACE. On Windows `st_ctime` is the creation time."""
    try:
        return path.stat().st_ctime
    except OSError:
        return None


def _icacls(*args: str) -> bool:
    done = subprocess.run(
        ["icacls", *args, "/Q"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return done.returncode == 0


def _has_ace(path: Path, sid: str) -> bool:
    try:
        listed = subprocess.run(
            ["icacls", str(path)], capture_output=True, text=True, errors="replace", check=False
        )
    except OSError:
        return False
    # icacls prints a capability SID it cannot name as the SID itself.
    return listed.returncode == 0 and f"{sid}:" in listed.stdout


@dataclass
class _Grant:
    path: str
    access: Access
    sid: str
    folder_id: float | None = None

    def key(self) -> tuple[str, Access]:
        return (os.path.normcase(self.path), self.access)


@dataclass
class FolderGrants:
    """The ACEs coscribe wrote on the person's folders."""

    state_dir: Path
    _grants: dict[tuple[str, Access], _Grant] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        try:
            raw = json.loads((self.state_dir / _REGISTRY).read_text(encoding="utf-8"))
            for entry in raw.get("grants", []):
                grant = _Grant(**entry)
                self._grants[grant.key()] = grant
        except (OSError, ValueError, TypeError):
            pass

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        target = self.state_dir / _REGISTRY
        data = {"grants": [vars(g) for g in self._grants.values()]}
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(temp, target)

    def entries(self) -> list[tuple[str, Access]]:
        return [(g.path, g.access) for g in self._grants.values()]

    def ensure(self, path: Path, access: Access) -> tuple[str, bool]:
        """The capability that opens `path`, and whether its ACE is in place. A folder already
        granted costs nothing; a failed grant (no right to change the ACL) is not recorded, so
        the next run tries again."""
        resolved = path.resolve()
        name = capability_name(resolved, access)
        folder_id = _folder_id(resolved)
        with _lock:
            known = self._grants.get((os.path.normcase(str(resolved)), access))
            if known is not None and known.folder_id == folder_id:
                return name, True
            sid = _appcontainer.capability_sid(name)
            grant = _Grant(str(resolved), access, sid, folder_id)
            self._grants[grant.key()] = grant
            self._save()
            # The ACE outlives a lost registry (a test's fresh state folder, a reinstall):
            # reading the ACL costs far less than walking a large tree again.
            ok = _has_ace(resolved, sid) or _icacls(
                str(resolved), "/grant", f"*{sid}:(OI)(CI)({_RIGHTS[access]})"
            )
            if not ok:
                del self._grants[grant.key()]
                self._save()
            return name, ok

    def revoke(self, path: Path) -> None:
        """Takes both ACEs off a folder the person removed."""
        key = os.path.normcase(str(path.resolve()))
        with _lock:
            for access in ("read", "write"):
                grant = self._grants.get((key, access))
                if grant is None:
                    continue
                # A folder that is gone has no ACE left to remove.
                if (
                    _icacls(grant.path, "/remove:g", f"*{grant.sid}")
                    or not Path(grant.path).exists()
                ):
                    del self._grants[(key, access)]
            self._save()

    def sweep(self, keep: Sequence[Path] | None) -> list[str]:
        """Revokes every grant on a folder not in `keep` (None: every grant, at uninstall), and
        returns the folders swept. A folder kept by a conversation that isn't open stays granted,
        so its next run doesn't walk the tree again."""
        kept = {os.path.normcase(str(p.resolve())) for p in keep or ()}
        gone = sorted(
            {g.path for g in self._grants.values() if os.path.normcase(g.path) not in kept}
        )
        for path in gone:
            self.revoke(Path(path))
        return gone


def capabilities_for(isolation: Isolation) -> tuple[list[str], list[Path]]:
    """The capabilities a run holds, and the folders whose grant failed (the run cannot reach
    them; the runtime ones may still be readable through the system's own ACL)."""
    grants = FolderGrants(isolation.state_dir)
    names: list[str] = []
    failed: list[Path] = []
    wanted: list[tuple[Path, Access]] = [
        *((p, "write") for p in isolation.writable),
        *((p, "read") for p in (*isolation.readable, *isolation.runtime)),
    ]
    for path, access in wanted:
        if not path.exists():
            continue
        name, ok = grants.ensure(path, access)
        if ok:
            names.append(name)
        else:
            failed.append(path)
    if isolation.network:
        names.extend(NETWORK_CAPABILITIES)
    return names, failed


# Node looks up a script's real path through every folder above it, and the container may not
# look at C:\ (seen on the Windows runner); these flags keep the path it was given.
_NODE_CONTAINED = ("--preserve-symlinks", "--preserve-symlinks-main")


def start(
    argv: Sequence[str] | str, cwd: str, env: Mapping[str, str], isolation: Isolation
) -> ContainerProcess:
    if isinstance(argv, str):
        # cmd in the container can echo but not list a folder (`dir` is denied even in a
        # granted one, seen on the Windows runner), so a command line would fail at random.
        raise IsolationRefused(
            "Commands don't run on this computer: in the isolated space Windows' shell can't "
            "use the folders. Run the work as a Python or Node script instead "
            "(run_background_script, run_python_script or run_node_script)."
        )
    if argv and PureWindowsPath(argv[0]).stem.lower() == "node":
        argv = [argv[0], *_NODE_CONTAINED, *argv[1:]]
    capabilities, _failed = capabilities_for(isolation)
    return _appcontainer.start(argv, str(Path(cwd).resolve()), env, capabilities)


# ---- Self-test -----------------------------------------------------------------------------

_PROBE = r"""
import json, os, socket, sys
inside, outside = sys.argv[1], sys.argv[2]
def check(name, fn):
    try:
        fn()
        print(json.dumps({"check": name, "ok": True}), flush=True)
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        print(json.dumps({"check": name, "ok": False, "detail": detail}), flush=True)
def write(path):
    with open(path, "w") as fh:
        fh.write("x")
check("write inside", lambda: write(os.path.join(inside, "probe.txt")))
check("delete inside", lambda: os.remove(os.path.join(inside, "probe.txt")))
check("write outside", lambda: write(os.path.join(outside, "probe.txt")))
check("read outside", lambda: open(os.path.join(outside, "secret.txt")).read())
check("network", lambda: socket.create_connection(("1.1.1.1", 443), timeout=3).close())
"""

# What each check must do for the isolation to hold.
_EXPECTED = {
    "write inside": True,
    "delete inside": False,
    "write outside": False,
    "read outside": False,
    "network": False,
}


@dataclass(frozen=True)
class SelfTest:
    ok: bool
    # The check that differed, in the words Settings shows; empty when all held.
    failed: str = ""
    detail: str = ""


# Raised when the probe or what it checks changes, so every machine tests again.
_PROBE_REVISION = "1"


def _machine_key(python: Path) -> dict[str, str]:
    # The venv's python.exe keeps its path when the person chooses another Python to make it
    # from, so the installation it was made from is part of the key too.
    made_from = [str(p) for p in venv_runtime(python.parent.parent)[1:]]
    return {
        "windows": platform.version(),
        "python": str(python),
        "home": ";".join(made_from),
        "probe": _PROBE_REVISION,
    }


# The answer is about the machine, not the state folder: one test per process at most.
_tested: dict[str, SelfTest] = {}


def self_test(
    state_dir: Path, python: Path, runtime: Sequence[Path], *, fresh: bool = False
) -> SelfTest:
    """Whether a script started by `python` is held to its folders on this machine. Cached until
    Windows, the interpreter or coscribe changes; `fresh` runs it again (Settings' re-test)."""
    if not available():
        return SelfTest(False, "not Windows")
    if not fresh:
        cached = _cached_self_test(state_dir, python)
        if cached is not None:
            return cached
    key = _machine_key(python)
    result = _run_self_test(state_dir, python, runtime)
    (state_dir / _SELF_TEST).write_text(
        json.dumps({"key": key, "result": vars(result)}), encoding="utf-8"
    )
    _tested[json.dumps(key, sort_keys=True)] = result
    return result


def _cached_self_test(state_dir: Path, python: Path) -> SelfTest | None:
    """The last result for this Windows, interpreter and probe, without running anything."""
    key = _machine_key(python)
    memo = json.dumps(key, sort_keys=True)
    if memo in _tested:
        return _tested[memo]
    try:
        saved = json.loads((state_dir / _SELF_TEST).read_text(encoding="utf-8"))
        if saved.get("key") == key:
            _tested[memo] = SelfTest(**saved["result"])
            return _tested[memo]
    except (OSError, ValueError, TypeError, KeyError):
        pass
    return None


def venv_runtime(venv_dir: Path) -> list[Path]:
    """What a venv's python.exe reads to start: the venv and the installation it was made from
    (`home` in pyvenv.cfg), whose DLLs and standard library the launcher loads."""
    folders = [venv_dir]
    try:
        for line in (venv_dir / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "home" and value.strip():
                folders.append(Path(value.strip()))
    except OSError:
        pass
    return folders


def sweep_folder_grants(state_dir: Path, folders: Sequence[Path] | None) -> list[str]:
    """Takes coscribe's ACEs off folders no longer in use: `folders` are the person's folders
    still configured or used by a conversation (None at uninstall: every grant goes). What the
    interpreters read is kept, and a self-test's folder, deleted after the test, is dropped."""
    keep: list[Path] | None = None
    if folders is not None:
        node = shutil.which("node")
        keep = [
            *folders,
            *venv_runtime(state_dir / "script-env"),
            state_dir / "node-env",
            state_dir / "script-scratch",
            *([Path(node).resolve().parent] if node else []),
        ]
    return FolderGrants(state_dir).sweep(keep)


def scratch_root(state_dir: Path) -> Path:
    """Where a run's own files go (the script text, TEMP): one folder granted once, instead of
    the system temp folder, which a container cannot reach."""
    root = state_dir / "script-scratch"
    root.mkdir(parents=True, exist_ok=True)
    return root


class IsolationRefused(ValueError):
    """A script was not started: this machine failed the self-test and the person has not let
    this conversation run scripts without isolation."""


_UNISOLATED = "unisolated-conversations.json"


class UnisolatedConversations:
    """The conversations whose person chose, in Edit environment, to run scripts without isolation
    on a machine that failed the self-test. The choice itself is audited by its caller."""

    def __init__(self, state_dir: Path) -> None:
        self._path = Path(state_dir) / _UNISOLATED

    def _read(self) -> dict[str, str]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

    def allowed(self, thread_id: str | None) -> bool:
        return thread_id is not None and thread_id in self._read()

    def set(self, thread_id: str, allowed: bool, when: str) -> None:
        with _lock:
            data = self._read()
            if allowed:
                data[thread_id] = when
            else:
                data.pop(thread_id, None)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(data), encoding="utf-8")


def for_scripts(
    state_dir: Path,
    *,
    writable: Sequence[Path],
    readable: Sequence[Path] = (),
    runtime: Sequence[Path] = (),
    thread_id: str | None = None,
) -> Isolation | None:
    """The isolation for one run, or None when the run goes on as before, with the folder guard
    only: not Windows, or a failed self-test in a conversation allowed to run without isolation.
    A failed self-test otherwise refuses the run. The run has the network only when the
    conversation allows full access (Edit environment > Network). The test runs on the script
    venv's Python, so a Node run makes that venv first if it is not there yet."""
    if not available():
        return None
    venv = state_dir / "script-env"
    python = venv_python(venv)
    if not python.is_file():
        ensure_script_env(state_dir)
    python_runtime = venv_runtime(venv)
    _probe_result = self_test(state_dir, python, python_runtime)
    print("PROBE for_scripts", state_dir, python_runtime, _probe_result, flush=True)
    if not _probe_result.ok:
        if UnisolatedConversations(state_dir).allowed(thread_id):
            return None
        raise IsolationRefused(
            "Scripts are off on this computer: coscribe's isolation check failed, so a script "
            "could reach more than this conversation's folders. Tell the person; they can see "
            "the check, test again, or allow scripts without isolation for this conversation "
            "under Edit environment > Scripts. Use the built-in tools meanwhile."
        )
    return Isolation(
        state_dir,
        writable=(*writable, scratch_root(state_dir)),
        readable=tuple(readable),
        runtime=(*python_runtime, *runtime),
        network=NetworkGuard(state_dir, thread_id).scripts_online(),
    )


def isolation_status(state_dir: Path, *, run: bool = False) -> dict[str, str]:
    """What Edit environment > Scripts shows: `passed`, `failed` (with the check that differed),
    `untested` (no result for this machine yet, or no script venv to test with), `setup_failed`
    (the venv to test with could not be made, and why) or `unsupported` (not Windows). Only `run`
    starts the test, making the venv first when there is none, so reading the status changes
    nothing."""
    venv = state_dir / "script-env"
    python = venv_python(venv)
    if not available():
        return {"state": "unsupported", "failed": "", "detail": ""}
    if not python.is_file():
        if not run:
            return {"state": "untested", "failed": "", "detail": ""}
        try:
            ensure_script_env(state_dir)
        except RuntimeError as exc:
            return {"state": "setup_failed", "failed": "", "detail": str(exc)[-600:]}
    if run:
        result: SelfTest | None = self_test(state_dir, python, venv_runtime(venv), fresh=True)
    else:
        result = _cached_self_test(state_dir, python)
    if result is None:
        return {"state": "untested", "failed": "", "detail": ""}
    state = "passed" if result.ok else "failed"
    return {"state": state, "failed": result.failed, "detail": result.detail}


_DENIED = ("PermissionError", "Access is denied", "EPERM", "EACCES")

# From 3.13 on Python resolves its own path as it starts, a call the container is denied; it runs
# on regardless, so the line it prints would only make the model think the script failed.
_REALPATH_WARNING = re.compile(r"^Failed to find real location of [^\n]*\n?", re.MULTILINE)


def quiet_stderr(stderr: str) -> str:
    """A contained script's stderr without the line every Python from 3.13 on prints there."""
    return _REALPATH_WARNING.sub("", stderr)


def denied_note(stderr: str) -> str:
    """Said when a contained script was refused, so the model reaches for the tool that can."""
    if not any(mark in stderr for mark in _DENIED):
        return ""
    return (
        "\n[This script ran isolated: it can write in the workspace and the read-write folders "
        "but cannot delete, rename or move files, and cannot reach folders not added to this "
        "conversation. Use delete_file or move_file, or ask the user to add the folder.]"
    )


def _run_self_test(state_dir: Path, python: Path, runtime: Sequence[Path]) -> SelfTest:
    state_dir.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="isolation-test-", dir=state_dir)).resolve()
    inside, outside = base / "inside", base / "outside"
    inside.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("x", encoding="utf-8")
    script = inside / "probe.py"
    script.write_text(_PROBE, encoding="utf-8")
    isolation = Isolation(state_dir, writable=(inside,), runtime=tuple(runtime), network=False)
    try:
        try:
            proc = start(
                [str(python), "-I", str(script), str(inside), str(outside)],
                str(inside),
                {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"},
                isolation,
            )
            # Both pipes at once: a child blocked on a full stderr would never finish stdout.
            errors: list[bytes] = []
            reader = threading.Thread(target=lambda: errors.append(proc.stderr.read()))
            reader.start()
            out = proc.stdout.read()
            reader.join()
            err = errors[0] if errors else b""
            proc.wait(60)
        except (ContainerUnavailable, OSError, subprocess.TimeoutExpired) as exc:
            return SelfTest(False, "start a script in isolation", str(exc)[:300])
        rows = {}
        for line in out.decode("utf-8", "replace").splitlines():
            try:
                row = json.loads(line)
                rows[row["check"]] = row
            except (ValueError, KeyError):
                continue
        if not rows:
            text = err.decode("utf-8", "replace").strip()
            return SelfTest(False, "start a script in isolation", text[-300:])
        for check, want in _EXPECTED.items():
            row = rows.get(check)
            if row is None or row["ok"] != want:
                detail = "" if row is None else str(row.get("detail", ""))
                return SelfTest(False, check, detail[:300])
        return SelfTest(True)
    finally:
        FolderGrants(state_dir).revoke(inside)
        shutil.rmtree(base, ignore_errors=True)

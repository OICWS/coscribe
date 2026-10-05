"""Where LibreOffice lives, and how to run it. Its installer doesn't put
`soffice` on PATH on Windows, so asking PATH alone left previews, layout
checks and recalculation silently off for anyone who installed it the
ordinary way."""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

_PROFILE_ROOT = Path(tempfile.gettempdir()) / "coscribe_lo_profiles"


def find_soffice() -> str | None:
    found = shutil.which("soffice")
    if found:
        return found
    candidates: list[Path] = []
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            base = os.environ.get(variable)
            if base:
                candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Programs" / "LibreOffice" / "program" / "soffice.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def _try_lock(handle: BinaryIO) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: BinaryIO) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def soffice_profile() -> Iterator[str]:
    """The `-env:UserInstallation=...` argument for one soffice run.

    Two soffice processes on one profile don't both work: the second hands
    its job to the first and exits with nothing made (or fails outright), so
    parallel tool calls lost previews and layout checks, and an open
    LibreOffice window blocked them all. Each concurrent run here gets a
    profile of its own; the profiles are kept and reused, since building
    one on first launch can take half a minute on Windows. The claim on a
    profile is an OS file lock, so it holds across processes (the web
    server and a `--check-wakes` run) and lapses if its holder dies."""
    _PROFILE_ROOT.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        handle = (_PROFILE_ROOT / f"slot{index}.lock").open("a+b")
        if _try_lock(handle):
            break
        handle.close()
        index += 1
    try:
        profile = _PROFILE_ROOT / f"slot{index}"
        profile.mkdir(exist_ok=True)
        yield f"-env:UserInstallation={profile.as_uri()}"
    finally:
        _unlock(handle)
        handle.close()


def run_soffice(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    """`subprocess.run(command, capture_output=True, timeout=timeout,
    check=True)`, except that a timeout kills LibreOffice too, not only its
    launcher: `soffice` starts `soffice.bin` and a plain timeout leaves that
    running, still holding its profile, so a hung one made every later run
    on the profile fail."""
    if sys.platform == "win32":
        group: dict[str, int | bool] = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        group = {"start_new_session": True}
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **group  # type: ignore[call-overload]
    ) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True
                )
            else:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command, stdout, stderr)
    return subprocess.CompletedProcess(command, 0, stdout, stderr)

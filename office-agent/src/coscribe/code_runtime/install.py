"""The pinned Codex CLI, downloaded into state_dir the first time the code
module is used -- never bundled: the binary alone is ~330 MB unpacked,
against a ~190 MB portable build.

Only two members of the PyPI wheel are kept (the `codex` binary and its
`rg`), fetched with HTTP range requests out of the wheel's zip, and each
is checked against the sha256 in _pins.py, which scripts/pin_codex.py
recorded from a wheel whose full hash matched PyPI's. A host that ignores
range requests (some corporate mirrors) gets the whole wheel instead,
checked against its own pinned hash first.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, BinaryIO

from ._pins import CODEX_VERSION, WHEELS

_TIMEOUT = 60.0
_CHUNK = 1 << 20

Progress = Callable[[int, int], None]


class CodexUnavailable(RuntimeError):
    pass


def _platform_key() -> str:
    machine = platform.machine().lower()
    if sys.platform == "linux" and machine == "arm64":
        machine = "aarch64"
    if sys.platform == "win32" and machine == "x86_64":
        machine = "amd64"
    return f"{sys.platform}/{machine}"


def _wheel() -> dict[str, Any]:
    key = _platform_key()
    if key not in WHEELS:
        raise CodexUnavailable(f"The code module has no Codex build for this machine ({key}).")
    return WHEELS[key]


def install_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "codex" / CODEX_VERSION


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def codex_executable(state_dir: Path) -> Path:
    return install_dir(state_dir) / "bin" / _exe("codex")


def codex_path_dir(state_dir: Path) -> Path:
    """Holds `rg`; Codex looks for it on PATH."""
    return install_dir(state_dir) / "codex-path"


def installed(state_dir: Path) -> bool:
    return codex_executable(state_dir).is_file()


def _member_target(root: Path, name: str) -> Path:
    # codex_cli_bin/bin/codex -> <root>/bin/codex
    return root.joinpath(*name.split("/")[1:])


class _RangeReader(io.RawIOBase):
    """A seekable view of a remote file, one HTTP range request per read,
    so zipfile reads the central directory and the wanted members only."""

    def __init__(self, url: str, size: int) -> None:
        self._url = url
        self._size = size
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self._size}[whence]
        self._pos = base + offset
        return self._pos

    def readinto(self, buffer: Any) -> int:
        count = min(len(buffer), self._size - self._pos)
        if count <= 0:
            return 0
        request = urllib.request.Request(
            self._url, headers={"Range": f"bytes={self._pos}-{self._pos + count - 1}"}
        )
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            if response.status != 206:
                raise _NoRangeSupport
            data = response.read()
        buffer[: len(data)] = data
        self._pos += len(data)
        return len(data)


class _NoRangeSupport(Exception):
    pass


def _supports_ranges(url: str) -> bool:
    request = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            return bool(response.status == 206)
    except OSError:
        return False


def _copy_member(
    wheel: zipfile.ZipFile,
    name: str,
    expected: dict[str, Any],
    target: Path,
    progress: Progress | None,
    done: list[int],
    total: int,
) -> None:
    digest = hashlib.sha256()
    target.parent.mkdir(parents=True, exist_ok=True)
    with wheel.open(name) as source, open(target, "wb") as sink:
        while chunk := source.read(_CHUNK):
            digest.update(chunk)
            sink.write(chunk)
            done[0] += len(chunk)
            if progress is not None:
                progress(done[0], total)
    if digest.hexdigest() != expected["sha256"]:
        raise CodexUnavailable(
            f"The downloaded {target.name} doesn't match the pinned Codex {CODEX_VERSION} "
            "(sha256 mismatch), so it wasn't installed."
        )
    if sys.platform != "win32":
        target.chmod(0o755)


def _extract(source: BinaryIO, pins: dict[str, Any], root: Path, progress: Progress | None) -> None:
    members: dict[str, dict[str, Any]] = pins["members"]
    total = sum(int(m["size"]) for m in members.values())
    done = [0]
    with zipfile.ZipFile(source) as wheel:
        for name, expected in members.items():
            _copy_member(wheel, name, expected, _member_target(root, name), progress, done, total)


def _download_whole(url: str, pins: dict[str, Any], spool: BinaryIO) -> None:
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=_TIMEOUT) as response:
        while chunk := response.read(_CHUNK):
            digest.update(chunk)
            spool.write(chunk)
    if digest.hexdigest() != pins["sha256"]:
        raise CodexUnavailable(
            f"The downloaded Codex {CODEX_VERSION} package doesn't match its pinned sha256, "
            "so it wasn't installed."
        )
    spool.seek(0)


def _fetch(url: str, pins: dict[str, Any], staging: Path, progress: Progress | None) -> None:
    if _supports_ranges(url):
        reader = io.BufferedReader(_RangeReader(url, int(pins["size"])), buffer_size=_CHUNK)
        try:
            _extract(reader, pins, staging, progress)
            return
        except _NoRangeSupport:
            pass
    with tempfile.TemporaryFile(dir=staging) as spool:
        _download_whole(url, pins, spool)
        _extract(spool, pins, staging, progress)


def ensure_codex(
    state_dir: Path, wheel_url: str | None = None, progress: Progress | None = None
) -> Path:
    """The Codex executable, downloading it first if this version isn't
    installed yet -- from PyPI, or `wheel_url` (a mirror of the same wheel;
    the pinned hashes still apply). Raises CodexUnavailable with a message
    for the user."""
    if installed(state_dir):
        return codex_executable(state_dir)
    pins = _wheel()
    url = wheel_url or str(pins["url"])
    final = install_dir(state_dir)
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{CODEX_VERSION}-", dir=final.parent))
    try:
        try:
            _fetch(url, pins, staging, progress)
        except (OSError, zipfile.BadZipFile) as exc:
            raise CodexUnavailable(f"Couldn't download Codex {CODEX_VERSION}: {exc}") from exc
        # The finished directory appears in one rename, so a half-written
        # install never looks installed.
        shutil.rmtree(final, ignore_errors=True)
        os.replace(staging, final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return codex_executable(state_dir)

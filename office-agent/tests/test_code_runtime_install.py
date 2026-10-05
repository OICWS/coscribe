"""Downloading the pinned Codex: two members out of the wheel by range
request, hash-checked, with a whole-wheel fallback."""

from __future__ import annotations

import hashlib
import io
import os
import re
import sys
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from coscribe.code_runtime import _pins, install
from coscribe.code_runtime.install import CodexUnavailable, codex_executable, ensure_codex

CODEX = b"#!/bin/sh\necho fake codex\n" * 1000
RG = b"fake rg\n" * 500
PADDING = os.urandom(400_000)


def _wheel_bytes() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as wheel:
        wheel.writestr("codex_cli_bin/bin/codex-code-mode-host", PADDING)
        wheel.writestr("codex_cli_bin/bin/codex", CODEX)
        wheel.writestr("codex_cli_bin/codex-path/rg", RG)
    return buffer.getvalue()


WHEEL = _wheel_bytes()


def _fake_pins(codex: bytes = CODEX, wheel: bytes = WHEEL) -> dict[str, Any]:
    return {
        "filename": "fake.whl",
        "url": "unused",
        "sha256": hashlib.sha256(wheel).hexdigest(),
        "size": len(wheel),
        "members": {
            "codex_cli_bin/bin/codex": {
                "sha256": hashlib.sha256(codex).hexdigest(),
                "size": len(codex),
            },
            "codex_cli_bin/codex-path/rg": {
                "sha256": hashlib.sha256(RG).hexdigest(),
                "size": len(RG),
            },
        },
    }


class _Server:
    def __init__(self, ranges: bool) -> None:
        self.ranges = ranges
        self.served = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_GET(self) -> None:
                match = re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers.get("Range") or "")
                if owner.ranges and match:
                    start, end = int(match[1]), min(int(match[2]), len(WHEEL) - 1)
                    body = WHEEL[start : end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(WHEEL)}")
                else:
                    body = WHEEL
                    self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                owner.served += len(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/fake.whl"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def pinned(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    pins = _fake_pins()
    monkeypatch.setattr(install, "WHEELS", {install._platform_key(): pins})
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    yield pins


@pytest.fixture
def ranged() -> Iterator[_Server]:
    server = _Server(ranges=True)
    yield server
    server.close()


@pytest.fixture
def unranged() -> Iterator[_Server]:
    server = _Server(ranges=False)
    yield server
    server.close()


def _leftovers(state_dir: Path) -> list[str]:
    root = state_dir / "codex"
    return sorted(p.name for p in root.iterdir()) if root.is_dir() else []


def test_only_the_two_needed_members_are_downloaded(
    pinned: dict[str, Any], ranged: _Server, tmp_path: Path
) -> None:
    progress: list[tuple[int, int]] = []

    executable = ensure_codex(
        tmp_path, ranged.url, lambda done, total: progress.append((done, total))
    )

    assert executable == codex_executable(tmp_path)
    assert executable.read_bytes() == CODEX
    assert (install.codex_path_dir(tmp_path) / "rg").read_bytes() == RG
    if sys.platform != "win32":
        assert os.access(executable, os.X_OK)
    assert ranged.served < len(WHEEL) - len(PADDING) // 2
    assert progress[-1] == (len(CODEX) + len(RG), len(CODEX) + len(RG))
    assert _leftovers(tmp_path) == [install.CODEX_VERSION]


def test_a_host_without_ranges_gets_the_whole_wheel_checked(
    pinned: dict[str, Any], unranged: _Server, tmp_path: Path
) -> None:
    executable = ensure_codex(tmp_path, unranged.url)

    assert executable.read_bytes() == CODEX
    assert unranged.served >= len(WHEEL)
    assert _leftovers(tmp_path) == [install.CODEX_VERSION]


def test_a_member_that_doesnt_match_its_pin_is_not_installed(
    monkeypatch: pytest.MonkeyPatch, pinned: dict[str, Any], ranged: _Server, tmp_path: Path
) -> None:
    monkeypatch.setattr(install, "WHEELS", {install._platform_key(): _fake_pins(codex=b"other")})

    with pytest.raises(CodexUnavailable, match="sha256"):
        ensure_codex(tmp_path, ranged.url)

    assert not install.installed(tmp_path)
    assert _leftovers(tmp_path) == []


def test_a_whole_wheel_that_doesnt_match_its_pin_is_not_installed(
    monkeypatch: pytest.MonkeyPatch, pinned: dict[str, Any], unranged: _Server, tmp_path: Path
) -> None:
    monkeypatch.setattr(install, "WHEELS", {install._platform_key(): _fake_pins(wheel=b"other")})

    with pytest.raises(CodexUnavailable, match="sha256"):
        ensure_codex(tmp_path, unranged.url)

    assert not install.installed(tmp_path)


def test_an_installed_codex_is_used_without_downloading(
    pinned: dict[str, Any], ranged: _Server, tmp_path: Path
) -> None:
    ensure_codex(tmp_path, ranged.url)
    served = ranged.served

    assert ensure_codex(tmp_path, "http://127.0.0.1:9/unreachable") == codex_executable(tmp_path)
    assert ranged.served == served


def test_an_unreachable_host_is_a_clear_error(pinned: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(CodexUnavailable, match="Couldn't download"):
        ensure_codex(tmp_path, "http://127.0.0.1:9/unreachable")
    assert _leftovers(tmp_path) == []


def test_a_machine_without_a_build_is_told_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(install, "_platform_key", lambda: "sunos/sparc")

    with pytest.raises(CodexUnavailable, match="sunos/sparc"):
        ensure_codex(tmp_path)


def test_every_pinned_build_has_both_members_and_full_hashes() -> None:
    assert set(_pins.WHEELS) == {
        "win32/amd64",
        "win32/arm64",
        "darwin/arm64",
        "darwin/x86_64",
        "linux/x86_64",
        "linux/aarch64",
    }
    for key, wheel in _pins.WHEELS.items():
        assert f"-{_pins.CODEX_VERSION}-" in wheel["filename"]
        assert re.fullmatch(r"[0-9a-f]{64}", wheel["sha256"])
        suffix = ".exe" if key.startswith("win32/") else ""
        assert set(wheel["members"]) == {
            f"codex_cli_bin/bin/codex{suffix}",
            f"codex_cli_bin/codex-path/rg{suffix}",
        }
        for member in wheel["members"].values():
            assert re.fullmatch(r"[0-9a-f]{64}", member["sha256"])
            assert member["size"] > 0

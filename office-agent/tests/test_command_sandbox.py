"""Commands in Codex's Windows sandbox (decision 0022): when it counts as set up, and the
permission profile and command line a command gets. The sandbox itself runs only on Windows;
`test_windows_real.py` runs a real command in it."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from coscribe.tools.exec import (
    command_sandbox,
    record_sandbox_setup,
    sandbox_home,
    sandbox_status,
    sandboxed_command,
)


@pytest.fixture
def on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(command_sandbox, "supported", lambda: True)


def _codex(tmp_path: Path) -> Path:
    codex = tmp_path / "codex.exe"
    codex.write_text("")
    return codex


@pytest.mark.skipif(sys.platform == "win32", reason="the answer off Windows")
def test_off_windows_it_is_unsupported_and_commands_are_not_wrapped(tmp_path: Path) -> None:
    record_sandbox_setup(tmp_path, _codex(tmp_path))
    assert sandbox_status(tmp_path)["state"] == "unsupported"
    assert sandboxed_command(tmp_path, "dir", tmp_path, [tmp_path], [], False) is None


@pytest.mark.usefixtures("on_windows")
def test_it_is_ready_only_while_the_codex_that_set_it_up_is_still_there(tmp_path: Path) -> None:
    assert sandbox_status(tmp_path) == {"state": "off", "detail": ""}
    record_sandbox_setup(tmp_path, None, "The administrator said no.")
    assert sandbox_status(tmp_path) == {"state": "failed", "detail": "The administrator said no."}
    codex = _codex(tmp_path)
    record_sandbox_setup(tmp_path, codex)
    assert sandbox_status(tmp_path)["state"] == "ready"
    codex.unlink()
    assert sandbox_status(tmp_path)["state"] == "off"


@pytest.mark.usefixtures("on_windows")
def test_a_command_runs_through_cmd_in_a_profile_for_exactly_its_folders(tmp_path: Path) -> None:
    codex = _codex(tmp_path)
    record_sandbox_setup(tmp_path, codex)
    root, extra = tmp_path / "work", tmp_path / "reference"

    argv, env = sandboxed_command(tmp_path, 'echo "hi"', root, [root], [extra], False) or ([], {})

    home = sandbox_home(tmp_path)
    assert env == {"CODEX_HOME": str(home)}
    profile = argv[3]
    script = Path(argv[-1])
    assert argv == [
        str(codex), "sandbox", "--permission-profile", profile, "-C", str(root), "--",
        "cmd.exe", "/d", "/c", str(script),
    ]  # fmt: skip
    # The line goes in a batch file: Codex would turn its quotes into \" for cmd.
    assert script.read_bytes() == b'@echo off\r\nchcp 65001 >nul\r\necho "hi"\r\n'
    assert script.parent == tmp_path / "command-sandbox" / "commands"
    config = (home / "config.toml").read_text(encoding="utf-8")
    assert config.startswith(
        'default_permissions = "coscribe-default"\n\n[windows]\nsandbox = "elevated"\n'
    )
    assert (
        f"[permissions.{profile}.filesystem]\n"
        '":root" = "read"\n'
        f'"{extra.as_posix()}" = "read"\n'
        f'"{script.parent.as_posix()}" = "read"\n'
        f'"{root.as_posix()}" = "write"\n\n'
        f"[permissions.{profile}.network]\n"
        "enabled = false\n"
    ) in config


@pytest.mark.usefixtures("on_windows")
def test_the_same_folders_share_a_profile_and_old_profiles_are_dropped(tmp_path: Path) -> None:
    record_sandbox_setup(tmp_path, _codex(tmp_path))

    def profile(folder: str, network: bool = False) -> str:
        argv, _ = sandboxed_command(
            tmp_path, "dir", tmp_path, [tmp_path / folder], [], network
        ) or ([], {})
        return argv[3]

    first = profile("a")
    assert profile("a") == first
    assert profile("a", network=True) != first
    for n in range(command_sandbox._PROFILES_KEPT):
        profile(f"b{n}")
    config = (sandbox_home(tmp_path) / "config.toml").read_text(encoding="utf-8")
    assert f"[permissions.{first}.filesystem]" not in config
    assert config.count(".filesystem]") == command_sandbox._PROFILES_KEPT + 1

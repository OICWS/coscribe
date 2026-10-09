"""A connector added as a bare `python` still starts when the app's PATH
has no usable Python."""

from __future__ import annotations

from pathlib import Path

import pytest

from coscribe.tools.mcp import with_interpreter

_SCRIPT_ENV = "coscribe.tools.script_env"


def _config(command: str) -> dict[str, object]:
    return {"type": "mcp", "name": "probe", "command": command}


def test_a_python_that_does_not_run_is_replaced_by_one_that_does(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(f"{_SCRIPT_ENV}.shutil.which", lambda name: "stub.exe")
    monkeypatch.setattr(
        f"{_SCRIPT_ENV}.working_interpreters",
        lambda candidates: [c for c in candidates if c == "D:/Python/python.exe"],
    )
    monkeypatch.setattr(f"{_SCRIPT_ENV}.fallbacks_for_platform", lambda: ["D:/Python/python.exe"])

    resolved = with_interpreter(_config("python"), tmp_path)  # type: ignore[arg-type]

    assert resolved["command"] == "D:/Python/python.exe"


def test_a_python_that_runs_and_other_commands_are_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(f"{_SCRIPT_ENV}.shutil.which", lambda name: "python.exe")
    monkeypatch.setattr(f"{_SCRIPT_ENV}.working_interpreters", list)

    for command in ("python", "npx", "C:/tools/python.exe"):
        assert with_interpreter(_config(command), tmp_path)["command"] == command  # type: ignore[arg-type]


def test_the_interpreter_chosen_in_settings_comes_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "script_env_interpreter.txt").write_text("E:/py/python.exe", encoding="utf-8")
    monkeypatch.setattr(f"{_SCRIPT_ENV}.shutil.which", lambda name: None)
    monkeypatch.setattr(f"{_SCRIPT_ENV}.working_interpreters", list)
    monkeypatch.setattr(f"{_SCRIPT_ENV}.fallbacks_for_platform", lambda: ["D:/Python/python.exe"])

    resolved = with_interpreter(_config("python3"), tmp_path)  # type: ignore[arg-type]

    assert resolved["command"] == "E:/py/python.exe"


def test_with_nothing_runnable_the_command_is_kept_so_the_usual_error_shows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(f"{_SCRIPT_ENV}.shutil.which", lambda name: None)
    monkeypatch.setattr(f"{_SCRIPT_ENV}.working_interpreters", lambda candidates: [])

    assert with_interpreter(_config("python"), tmp_path)["command"] == "python"  # type: ignore[arg-type]

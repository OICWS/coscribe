"""Settings › Capabilities: what coscribe may run on this computer (#588)."""

from __future__ import annotations

from pathlib import Path

import pytest

from coscribe.tools.exec import Capabilities, CapabilityOff, build_script_tools, require_capability


def test_scripts_are_on_and_commands_off_until_the_person_changes_them(tmp_path: Path) -> None:
    off = {"scripts": True, "commands": False, "commands_without_sandbox": False}
    assert Capabilities(tmp_path).get() == off
    Capabilities(tmp_path).set("commands", True)
    Capabilities(tmp_path).set("scripts", False)
    assert Capabilities(tmp_path).get() == {**off, "scripts": False, "commands": True}


def test_an_unknown_capability_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Capabilities(tmp_path).set("everything", True)


def test_a_turned_off_capability_refuses_and_says_where_to_turn_it_on(tmp_path: Path) -> None:
    require_capability(tmp_path, "scripts")
    with pytest.raises(CapabilityOff, match="Settings › Capabilities"):
        require_capability(tmp_path, "commands")


def test_commands_need_the_sandbox_or_the_second_confirmation(tmp_path: Path) -> None:
    Capabilities(tmp_path).set("commands", True)
    with pytest.raises(CapabilityOff, match="sandbox"):
        require_capability(tmp_path, "commands")
    Capabilities(tmp_path).set("commands_without_sandbox", True)
    require_capability(tmp_path, "commands")


def test_a_script_is_refused_before_anything_is_set_up_when_scripts_are_off(
    tmp_path: Path,
) -> None:
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    Capabilities(state).set("scripts", False)
    (run,) = build_script_tools(workspace, state)
    with pytest.raises(CapabilityOff):
        run(script="print('hi')", description="say hi")
    assert not (state / "script-env").exists()

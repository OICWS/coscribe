import sys
from pathlib import Path

from coscribe.runtime.hooks import HOOK_EVENTS, empty_hooks_config, load_hooks_config, run_hook

PY = sys.executable


# -- run_hook -----------------------------------------------------------------


def test_exit_zero_is_allowed() -> None:
    result = run_hook(f"{PY} -c 'exit(0)'", {})
    assert result.allowed is True
    assert result.reason is None


def test_exit_nonzero_with_stderr_uses_stderr_as_reason() -> None:
    result = run_hook(f"{PY} -c \"import sys; sys.stderr.write('nope'); sys.exit(1)\"", {})
    assert result.allowed is False
    assert result.reason == "nope"


def test_exit_nonzero_without_stderr_falls_back_to_generic_reason() -> None:
    result = run_hook(f"{PY} -c 'exit(3)'", {})
    assert result.allowed is False
    assert result.reason is not None
    assert "3" in result.reason


def test_timeout_is_denied_with_timeout_reason() -> None:
    result = run_hook(f"{PY} -c 'import time; time.sleep(5)'", {}, timeout=0.2)
    assert result.allowed is False
    assert "timed out" in (result.reason or "")


def test_payload_is_delivered_as_json_on_stdin() -> None:
    command = (
        f"{PY} -c \"import sys, json; "
        "d = json.load(sys.stdin); "
        "sys.exit(0 if d['tool_name'] == 'write_file' else 1)\""
    )
    allowed = run_hook(command, {"tool_name": "write_file"})
    denied = run_hook(command, {"tool_name": "read_file"})
    assert allowed.allowed is True
    assert denied.allowed is False


# -- load_hooks_config ----------------------------------------------------------


def test_load_hooks_config_parses_all_events(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text(
        '{"PreToolUse": ["cmd-a"], "PostToolUse": ["cmd-b"], "SessionStart": ["cmd-c"], '
        '"SessionEnd": ["cmd-d"], "UserPromptSubmit": ["cmd-e"], "PreCompact": ["cmd-f"], '
        '"PostCompact": ["cmd-g"], "Interrupt": ["cmd-h"]}'
    )

    config = load_hooks_config(path)

    assert config == {
        "PreToolUse": ["cmd-a"],
        "PostToolUse": ["cmd-b"],
        "SessionStart": ["cmd-c"],
        "SessionEnd": ["cmd-d"],
        "UserPromptSubmit": ["cmd-e"],
        "PreCompact": ["cmd-f"],
        "PostCompact": ["cmd-g"],
        "Interrupt": ["cmd-h"],
    }


def test_load_hooks_config_defaults_missing_events_to_empty(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text('{"PreToolUse": ["cmd-a"]}')

    config = load_hooks_config(path)

    expected = empty_hooks_config()
    expected["PreToolUse"] = ["cmd-a"]
    assert config == expected


def test_load_hooks_config_ignores_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "hooks.json"
    path.write_text('{"PreToolUse": ["cmd-a"], "SomeFutureEvent": ["cmd-z"]}')

    config = load_hooks_config(path)

    assert "SomeFutureEvent" not in config
    assert config["PreToolUse"] == ["cmd-a"]


def test_empty_hooks_config_has_every_event_with_no_commands() -> None:
    config = empty_hooks_config()
    assert set(config) == set(HOOK_EVENTS)
    assert all(commands == [] for commands in config.values())

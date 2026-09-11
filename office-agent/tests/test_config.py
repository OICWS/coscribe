from pathlib import Path

import pytest
from pydantic import ValidationError

from coscribe.config import Settings


def test_requires_default_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COSCRIBE_DEFAULT_MODEL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_default_model_must_have_provider_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "claude-sonnet-4-5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.setenv("COSCRIBE_WORKSPACE_ROOT", "/tmp/some-workspace")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.default_model == "anthropic:claude-sonnet-4-5"
    assert settings.workspace_root == Path("/tmp/some-workspace")


def test_defaults_without_optional_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.delenv("COSCRIBE_WORKSPACE_ROOT", raising=False)
    monkeypatch.delenv("COSCRIBE_STATE_DIR", raising=False)
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.delenv("COSCRIBE_SKILLS_DIR", raising=False)
    monkeypatch.delenv("COSCRIBE_HOOKS_CONFIG_PATH", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.workspace_root == Path("./workspace")
    assert settings.state_dir == Path(".coscribe/state")
    assert settings.log_level == "INFO"
    assert settings.mcp_config_path is None
    assert settings.skills_dir == Path("./skills")
    assert settings.hooks_config_path is None


def test_mcp_config_path_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.setenv("COSCRIBE_MCP_CONFIG_PATH", "/tmp/mcp.json")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.mcp_config_path == Path("/tmp/mcp.json")


def test_skills_dir_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.setenv("COSCRIBE_SKILLS_DIR", "/tmp/my-skills")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.skills_dir == Path("/tmp/my-skills")


def test_hooks_config_path_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.setenv("COSCRIBE_HOOKS_CONFIG_PATH", "/tmp/hooks.json")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.hooks_config_path == Path("/tmp/hooks.json")


def test_blank_mcp_config_path_env_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression test: cp .env.example .env (the documented setup step) used
    # to leave COSCRIBE_MCP_CONFIG_PATH set to an empty string rather
    # than unset, and Path("") resolves to Path(".") -- silently pointing
    # config at the cwd instead of behaving like "no MCP config" (see
    # config.py's _blank_optional_path_is_unset).
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.setenv("COSCRIBE_MCP_CONFIG_PATH", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.mcp_config_path is None


def test_blank_hooks_config_path_env_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.setenv("COSCRIBE_HOOKS_CONFIG_PATH", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.hooks_config_path is None


def test_exec_policy_path_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.delenv("COSCRIBE_EXEC_POLICY_PATH", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.exec_policy_path is None


def test_exec_policy_path_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.setenv("COSCRIBE_EXEC_POLICY_PATH", "/tmp/exec_policy.json")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.exec_policy_path == Path("/tmp/exec_policy.json")


def test_blank_exec_policy_path_env_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.setenv("COSCRIBE_EXEC_POLICY_PATH", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.exec_policy_path is None


def test_extra_readable_dirs_default_to_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.delenv("COSCRIBE_EXTRA_READABLE_DIRS", raising=False)
    monkeypatch.delenv("COSCRIBE_EXTRA_WRITABLE_DIRS", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.extra_readable_dirs == []
    assert settings.extra_writable_dirs == []


def test_extra_dirs_parse_as_comma_separated_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    # Not a JSON array, unlike pydantic-settings' own default for list
    # fields -- matches every other .env value in this project (plain
    # unquoted strings), see config.py's _comma_separated_paths validator.
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.setenv("COSCRIBE_EXTRA_READABLE_DIRS", "/tmp/a, /tmp/b")
    monkeypatch.setenv("COSCRIBE_EXTRA_WRITABLE_DIRS", "/tmp/c")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.extra_readable_dirs == [Path("/tmp/a"), Path("/tmp/b")]
    assert settings.extra_writable_dirs == [Path("/tmp/c")]


def test_max_turns_defaults_to_20(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.delenv("COSCRIBE_MAX_TURNS", raising=False)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.max_turns == 20


def test_max_turns_loads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COSCRIBE_DEFAULT_MODEL", "anthropic:claude-sonnet-4-5")
    monkeypatch.setenv("COSCRIBE_MAX_TURNS", "8")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.max_turns == 8

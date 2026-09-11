"""Tests for loading user-added OpenAI-compatible LLM provider configs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coscribe.runtime.provider_config import load_custom_providers


def _write_config(tmp_path: Path, providers: dict[str, object]) -> Path:
    config_path = tmp_path / "providers.json"
    config_path.write_text(json.dumps({"providers": providers}))
    return config_path


def test_load_custom_providers_happy_path(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        {
            "deepseek": {
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-test",
                "default_model": "deepseek-v4-flash",
            }
        },
    )

    providers = load_custom_providers(config_path)

    assert providers == {
        "deepseek": {"base_url": "https://api.deepseek.com/v1", "api_key": "sk-test"}
    }


def test_load_custom_providers_missing_file_returns_empty(tmp_path: Path) -> None:
    # A configured-but-never-created path (COSCRIBE_PROVIDERS_CONFIG_PATH
    # set before any provider was ever added through the Settings UI, the
    # only thing that actually creates the file) must not crash startup --
    # live-hit: FileNotFoundError from create_app_lg's own unconditional
    # call, see runtime/provider_config.py's docstring.
    config_path = tmp_path / "providers.json"

    assert load_custom_providers(config_path) == {}


def test_load_custom_providers_requires_providers_key(tmp_path: Path) -> None:
    config_path = tmp_path / "providers.json"
    config_path.write_text(json.dumps({"servers": {}}))

    with pytest.raises(ValueError, match="providers"):
        load_custom_providers(config_path)


def test_load_custom_providers_rejects_entry_missing_base_url(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path, {"deepseek": {"api_key": "sk-test"}})

    with pytest.raises(ValueError, match="deepseek"):
        load_custom_providers(config_path)


def test_load_custom_providers_rejects_entry_missing_api_key(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path, {"deepseek": {"base_url": "https://api.deepseek.com/v1"}}
    )

    with pytest.raises(ValueError, match="deepseek"):
        load_custom_providers(config_path)

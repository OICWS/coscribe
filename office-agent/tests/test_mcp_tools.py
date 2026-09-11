"""Tests for MCP config loading.

Connecting to configured servers happens in runtime_lg/mcp.py now (see
tests/test_mcp_lg.py) -- this module only parses/validates the config
file itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from coscribe.tools.mcp import load_mcp_server_configs


def _write_config(tmp_path: Path, servers: dict[str, Any]) -> Path:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({"mcpServers": servers}))
    return config_path


def test_load_mcp_server_configs_validates_stdio_and_http_entries(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        {
            "fs": {"command": "npx", "args": ["-y", "server"], "env": {"FOO": "bar"}},
            "remote": {"server_url": "https://example.com/mcp"},
        },
    )

    configs = load_mcp_server_configs(config_path)

    assert set(configs) == {"fs", "remote"}
    assert configs["fs"]["type"] == "mcp"
    assert configs["fs"]["name"] == "fs"
    assert configs["fs"]["command"] == "npx"
    assert configs["remote"]["server_url"] == "https://example.com/mcp"


def test_load_mcp_server_configs_requires_mcpServers_key(tmp_path: Path) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({"servers": {}}))

    with pytest.raises(ValueError, match="mcpServers"):
        load_mcp_server_configs(config_path)

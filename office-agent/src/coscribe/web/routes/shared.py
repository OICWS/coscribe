"""Helpers several route modules use."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # office docs/PDFs, not video files


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def _read_mcp_servers_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"mcpServers": {}}
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw.get("mcpServers"), dict):
        raw["mcpServers"] = {}
    return raw


def _read_providers_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"providers": {}}
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw.get("providers"), dict):
        raw["providers"] = {}
    return raw

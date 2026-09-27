"""Per-tool permissions for connector (MCP) tools, set in Settings >
Connectors: "allow" runs a tool without asking, "ask" leaves it to the
conversation's approval mode (the default, so a tool nobody has set
behaves as before), "block" keeps it from the model entirely.

Kept in the state folder rather than mcp.json: the connector config is
something a user may hand-edit or copy between machines, and a stray
policy key would fail its validation.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Literal, cast

from ..runtime.types import get_tool_metadata, tool_metadata

ToolPolicy = Literal["allow", "ask", "block"]
POLICIES: tuple[ToolPolicy, ...] = ("allow", "ask", "block")
DEFAULT_POLICY: ToolPolicy = "ask"

_LOCK = threading.Lock()

# The risk a tool arrived with, kept so "ask" can put it back after an
# "allow" marked the tool READ.
_BASE_RISK_ATTR = "__coscribe_connector_base_risk__"


class ConnectorPermissions:
    """{server: {tool name: policy}} in one JSON file."""

    def __init__(self, state_dir: str | Path) -> None:
        self.path = Path(state_dir) / "connector_permissions.json"

    def load(self) -> dict[str, dict[str, ToolPolicy]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            str(server): {
                str(tool): cast(ToolPolicy, policy)
                for tool, policy in tools.items()
                if policy in POLICIES
            }
            for server, tools in raw.items()
            if isinstance(tools, dict)
        }

    def policy(self, server: str, tool: str) -> ToolPolicy:
        return self.load().get(server, {}).get(tool, DEFAULT_POLICY)

    def update(self, server: str, policies: Mapping[str, str]) -> dict[str, ToolPolicy]:
        bad = [f"{tool}: {policy!r}" for tool, policy in policies.items() if policy not in POLICIES]
        if bad:
            allowed = ", ".join(POLICIES)
            raise ValueError(f"Policies must be one of {allowed} -- got {', '.join(bad)}")
        with _LOCK:
            data = self.load()
            mine = data.setdefault(server, {})
            for tool, policy in policies.items():
                if policy == DEFAULT_POLICY:
                    mine.pop(tool, None)
                else:
                    mine[tool] = cast(ToolPolicy, policy)
            if not mine:
                data.pop(server, None)
            self._write(data)
            return dict(data.get(server, {}))

    def forget(self, server: str) -> None:
        with _LOCK:
            data = self.load()
            if data.pop(server, None) is not None:
                self._write(data)

    def _write(self, data: dict[str, dict[str, ToolPolicy]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)


def name_of(tool: Any) -> str:
    return str(getattr(tool, "name", None) or getattr(tool, "__name__", ""))


def connector_of(tool: Any) -> str | None:
    """The connector a tool came from, or None for a built-in tool."""
    category = get_tool_metadata(tool).category or ""
    return category[len("mcp:") :] if category.startswith("mcp:") else None


def is_read_only(tool: Any) -> bool:
    """The server's own readOnlyHint annotation; a tool without one is
    taken as able to change things."""
    return bool((getattr(tool, "metadata", None) or {}).get("readOnlyHint"))


def apply_connector_permissions(
    tools: Iterable[Any], permissions: Mapping[str, Mapping[str, str]]
) -> list[Any]:
    """The connector tools a conversation gets: blocked ones left out, the
    rest marked as needing approval or not. Marks the tool objects
    themselves, which every conversation shares -- the permissions are
    app-wide too."""
    result = []
    for tool in tools:
        server = connector_of(tool)
        if server is None:
            result.append(tool)
            continue
        policy = permissions.get(server, {}).get(name_of(tool), DEFAULT_POLICY)
        if policy == "block":
            continue
        base = getattr(tool, _BASE_RISK_ATTR, None)
        if base is None:
            base = get_tool_metadata(tool).risk_category
            setattr(tool, _BASE_RISK_ATTR, base)
        tool_metadata(
            tool,
            risk_category="READ" if policy == "allow" else base,
            category=f"mcp:{server}",
        )
        result.append(tool)
    return result

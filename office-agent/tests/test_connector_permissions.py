"""Tests for tools/connector_permissions.py: the per-tool policy store and
how it shapes the connector tools a conversation gets."""

from pathlib import Path
from typing import Any

import pytest
from langchain_core.tools import StructuredTool

from coscribe.runtime.types import get_tool_metadata, tool_metadata
from coscribe.tools.connector_permissions import (
    ConnectorPermissions,
    apply_connector_permissions,
    is_read_only,
)


def _tool(name: str, server: str = "notes", read_only: bool = False) -> Any:
    def run() -> str:
        return "ok"

    tool = StructuredTool.from_function(
        run, name=name, description="d", metadata={"readOnlyHint": read_only}
    )
    tool_metadata(tool, risk_category="EXTERNAL", category=f"mcp:{server}")
    return tool


def test_an_unset_tool_needs_approval_and_setting_it_back_forgets_it(tmp_path: Path) -> None:
    store = ConnectorPermissions(tmp_path)

    assert store.policy("notes", "notes_read") == "ask"
    assert store.update("notes", {"notes_read": "allow", "notes_delete": "block"}) == {
        "notes_read": "allow",
        "notes_delete": "block",
    }
    assert store.update("notes", {"notes_read": "ask"}) == {"notes_delete": "block"}
    assert store.load() == {"notes": {"notes_delete": "block"}}


def test_an_unknown_policy_is_refused_and_nothing_is_saved(tmp_path: Path) -> None:
    store = ConnectorPermissions(tmp_path)

    with pytest.raises(ValueError, match="must be one of allow, ask, block"):
        store.update("notes", {"notes_read": "sometimes"})
    assert store.load() == {}


def test_forgetting_a_connector_drops_its_policies(tmp_path: Path) -> None:
    store = ConnectorPermissions(tmp_path)
    store.update("notes", {"notes_read": "allow"})
    store.update("mail", {"mail_send": "block"})

    store.forget("notes")

    assert store.load() == {"mail": {"mail_send": "block"}}


def test_policies_decide_which_tools_a_conversation_gets_and_which_ask() -> None:
    read = _tool("notes_read", read_only=True)
    write, gone = _tool("notes_write"), _tool("notes_delete")

    def built_in() -> str:
        """A built-in tool."""
        return ""

    tools = apply_connector_permissions(
        [read, write, gone, built_in],
        {"notes": {"notes_read": "allow", "notes_delete": "block"}},
    )

    assert tools == [read, write, built_in]
    assert get_tool_metadata(read).requires_approval is False
    assert get_tool_metadata(write).requires_approval is True
    assert get_tool_metadata(read).category == "mcp:notes"
    assert is_read_only(read) and not is_read_only(write)


def test_setting_a_tool_back_to_ask_gates_it_again() -> None:
    tool = _tool("notes_write")
    apply_connector_permissions([tool], {"notes": {"notes_write": "allow"}})

    apply_connector_permissions([tool], {})

    assert get_tool_metadata(tool).requires_approval is True


def test_ask_keeps_the_risk_a_tool_arrived_with() -> None:
    def fetch() -> str:
        """A connector tool its server marked read-only."""
        return ""

    tool_metadata(fetch, risk_category="READ", category="mcp:web")
    apply_connector_permissions([fetch], {})

    assert get_tool_metadata(fetch).requires_approval is False

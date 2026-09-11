from pathlib import Path

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.memory import build_memory_tools, format_memory_section, load_memory


def test_missing_file_returns_empty_string(tmp_path: Path) -> None:
    assert load_memory(tmp_path / "MEMORY.md") == ""


def test_remember_creates_file_and_parent_dirs(tmp_path: Path) -> None:
    memory_path = tmp_path / "nested" / "MEMORY.md"
    tools = {tool.__name__: tool for tool in build_memory_tools(memory_path)}  # type: ignore[attr-defined]

    result = tools["remember"](fact="The user prefers metric units.")

    assert result == "Remembered: The user prefers metric units."
    assert load_memory(memory_path) == "- The user prefers metric units."


def test_remember_appends_without_overwriting(tmp_path: Path) -> None:
    memory_path = tmp_path / "MEMORY.md"
    tools = {tool.__name__: tool for tool in build_memory_tools(memory_path)}  # type: ignore[attr-defined]

    tools["remember"](fact="First fact.")
    tools["remember"](fact="Second fact.")

    assert load_memory(memory_path) == "- First fact.\n- Second fact."


def test_remember_is_write_local_and_requires_approval(tmp_path: Path) -> None:
    """Reclassified from the old "low risk, no approval" as part of the
    Phase 4 risk taxonomy -- remember persists a durable, cross-session,
    system-prompt-injected fact, the same kind of consequential local
    side effect every other WRITE_LOCAL tool here already gates (see
    build_memory_tools' own comment)."""
    tools = {tool.__name__: tool for tool in build_memory_tools(tmp_path / "MEMORY.md")}  # type: ignore[attr-defined]

    metadata = get_tool_metadata(tools["remember"])

    assert metadata.risk_category == "WRITE_LOCAL"
    assert metadata.requires_approval is True


def test_format_memory_section() -> None:
    section = format_memory_section("- a fact")

    assert section == "Remembered facts from earlier sessions:\n- a fact"

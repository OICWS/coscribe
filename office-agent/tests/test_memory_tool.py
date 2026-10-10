from pathlib import Path

from coscribe.tools.memory import format_memory_section, load_memory


def test_missing_file_returns_empty_string(tmp_path: Path) -> None:
    assert load_memory(tmp_path / "MEMORY.md") == ""


def test_load_memory_strips_the_files_content(tmp_path: Path) -> None:
    path = tmp_path / "MEMORY.md"
    path.write_text("\n- a fact\n\n", encoding="utf-8")

    assert load_memory(path) == "- a fact"


def test_format_memory_section() -> None:
    assert format_memory_section("- a fact") == "The user's global instructions:\n- a fact"

"""The user's global instructions: one file they edit in Settings, read into
every conversation. The model only reads it -- nothing here writes to it, so
what it holds is what the user put there.
"""

from __future__ import annotations

from pathlib import Path


def load_memory(memory_path: str | Path) -> str:
    """Read the file's content, "" if it doesn't exist yet."""
    path = Path(memory_path)
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def format_memory_section(memory: str) -> str:
    return f"The user's global instructions:\n{memory}"

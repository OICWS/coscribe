"""Long-term memory: a small, curated file of durable facts, distinct from
a thread's own conversation history (which resets per conversation and
grows every turn).

Same "load static content / format for instructions / build the tool(s)"
split as tools/skills.py.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..runtime.types import tool_metadata


def load_memory(memory_path: str | Path) -> str:
    """Read the memory file's content, "" if it doesn't exist yet."""
    path = Path(memory_path)
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def format_memory_section(memory: str) -> str:
    return f"Remembered facts from earlier sessions:\n{memory}"


def build_memory_tools(memory_path: str | Path) -> list[Callable[..., Any]]:
    """Return the remember tool callable, bound to the given memory file."""
    path = Path(memory_path)

    def remember(fact: str) -> str:
        """Save a short, durable fact worth recalling in future sessions
        (e.g. a user preference or a stable project detail) -- not a running
        log or task status, keep this list short and curated.

        Args:
            fact: one short, self-contained fact to remember
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {fact.strip()}\n")
        return f"Remembered: {fact.strip()}"

    # WRITE_LOCAL, not READ -- it persists a fact to disk, injected into
    # every future session's own instructions (see format_memory_section
    # in coordinator.py). A real behavior change from the old "low risk,
    # no approval" classification, deliberately: it has exactly the kind
    # of durable, cross-session side effect every other WRITE_LOCAL tool
    # here already gates, and slipping through ungated looks like the old
    # taxonomy's own oversight rather than an intentional exception.
    return [tool_metadata(remember, risk_category="WRITE_LOCAL", category="memory")]

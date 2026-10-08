"""The documentation layout the sessions rely on: a short roadmap, a frozen
archive of the old one, and a log with one file per piece of work."""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = ROOT / "docs" / "history" / "roadmap-through-2026-10-08.md"
# Changing the archive is deliberate: update this hash in the same change.
ARCHIVE_SHA256 = "5805ef275d151b727c5d61ae1a22f3418ea2cbe46773f83fb0fd54054cc5d57f"


def test_the_roadmap_stays_a_short_index() -> None:
    lines = (ROOT / "office-agent" / "ROADMAP.md").read_text(encoding="utf-8").splitlines()

    assert len(lines) <= 300, (
        "ROADMAP.md is an index now. Log what shipped in docs/log/<date>-<slug>.md "
        "and keep open work in docs/plan.md."
    )


def test_the_archived_roadmap_is_not_edited() -> None:
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()

    assert digest == ARCHIVE_SHA256, (
        "docs/history/roadmap-through-2026-10-08.md is frozen. Put new entries in docs/log/."
    )


def test_every_log_entry_is_dated_and_titled() -> None:
    entries = [p for p in (ROOT / "docs" / "log").glob("*.md") if p.name != "README.md"]

    for entry in entries:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-[a-z0-9-]+\.md", entry.name), entry.name
        assert entry.read_text(encoding="utf-8").startswith("# "), entry.name


def test_claude_md_imports_agents_md() -> None:
    assert (ROOT / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"
    assert (ROOT / "AGENTS.md").is_file()

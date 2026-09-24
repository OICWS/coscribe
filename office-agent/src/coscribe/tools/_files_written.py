"""Which workspace files a script created or modified -- so a script's
output files show up alongside the ones document tools write, without the
model having to announce them."""

from __future__ import annotations

import os
from pathlib import Path

# A workspace can be a whole Documents folder: past this many files the
# walk stops and nothing is reported, keeping every script call cheap.
_MAX_FILES_SCANNED = 5000
MAX_FILES_REPORTED = 50

_SKIPPED_DIRS = frozenset({"node_modules", "__pycache__", "venv", ".venv"})

Snapshot = dict[str, tuple[int, int]]


def snapshot_workspace(root: Path) -> Snapshot | None:
    """(mtime_ns, size) per file under root, keyed by its /-separated
    relative path; None when the workspace is too big to scan."""
    files: Snapshot = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _SKIPPED_DIRS]
        for filename in filenames:
            if filename.startswith("."):
                continue
            full = Path(dirpath) / filename
            try:
                stat = full.stat()
            except OSError:
                continue
            files[full.relative_to(root).as_posix()] = (stat.st_mtime_ns, stat.st_size)
            if len(files) > _MAX_FILES_SCANNED:
                return None
    return files


def files_written(before: Snapshot | None, root: Path) -> list[str]:
    if before is None:
        return []
    after = snapshot_workspace(root)
    if after is None:
        return []
    changed = sorted(path for path, stamp in after.items() if before.get(path) != stamp)
    return changed[:MAX_FILES_REPORTED]


def with_files_written(
    outcome: dict[str, object], before: Snapshot | None, root: Path
) -> dict[str, object]:
    written = files_written(before, root)
    if written:
        outcome["files_written"] = written
    return outcome

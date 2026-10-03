"""Where LibreOffice lives. Its installer doesn't put `soffice` on PATH on
Windows, so asking PATH alone left previews, layout checks and recalculation
silently off for anyone who installed it the ordinary way."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def find_soffice() -> str | None:
    found = shutil.which("soffice")
    if found:
        return found
    candidates: list[Path] = []
    if sys.platform == "win32":
        for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
            base = os.environ.get(variable)
            if base:
                candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "Programs" / "LibreOffice" / "program" / "soffice.exe")
    elif sys.platform == "darwin":
        candidates.append(Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None

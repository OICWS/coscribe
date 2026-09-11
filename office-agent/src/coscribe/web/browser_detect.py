"""Best-effort local browser detection for the Playwright MCP connector --
Windows only for now. That connector needs a real Chromium-family browser
binary to actually drive, and a missing one used to surface as a cryptic
Node/pydantic stack trace mid-conversation instead of a clear, actionable
message at connector-setup time -- see the "Add" flow in web/app.py.
"""

from __future__ import annotations

import os
from pathlib import Path

# Checked in this order: Edge ships on every Windows install by default,
# so it's the most likely to already be present; Chrome as a fallback.
# Both 32/64-bit Program Files locations are checked since either is
# possible depending on how the browser was installed.
#
# Each candidate is (env var, path segments) rather than a "%VAR%\..."
# template string -- os.path.expandvars only expands %-style vars on
# native Windows (ntpath); on POSIX (posixpath) it's a no-op, which would
# make this both wrong to ever test off Windows and silently return None
# in production if that ever changed. Looking the env var up directly and
# joining segments via Path(...) works identically on every host OS.
_WINDOWS_CANDIDATES: list[tuple[str, tuple[str, ...]]] = [
    ("ProgramFiles(x86)", ("Microsoft", "Edge", "Application", "msedge.exe")),
    ("ProgramFiles", ("Microsoft", "Edge", "Application", "msedge.exe")),
    ("LocalAppData", ("Microsoft", "Edge", "Application", "msedge.exe")),
    ("ProgramFiles(x86)", ("Google", "Chrome", "Application", "chrome.exe")),
    ("ProgramFiles", ("Google", "Chrome", "Application", "chrome.exe")),
    ("LocalAppData", ("Google", "Chrome", "Application", "chrome.exe")),
]


def find_windows_browser() -> str | None:
    """First existing Chromium-family executable at a standard Windows
    install location, or None if none of them exist. Doesn't check
    sys.platform itself -- that's the caller's job (see
    web/app.py's /api/mcp/browser-check) -- so this stays a pure,
    OS-agnostic-to-test function: real path-construction/existence-check
    logic, exercisable via monkeypatched env vars + tmp_path on any host
    OS, not just Windows."""
    for env_var, segments in _WINDOWS_CANDIDATES:
        base = os.environ.get(env_var)
        if not base:
            continue
        path = Path(base, *segments)
        if path.is_file():
            return str(path)
    return None

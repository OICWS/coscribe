"""PyInstaller entry point for the desktop sidecar server.

Thin wrapper so PyInstaller has a concrete script to analyze -- the
`coscribe-web` console_script is generated package metadata, not a
file, so PyInstaller has nothing to point its static analysis at without
this. Runs the same `main()` the real console_script calls.
"""

from coscribe.web.app import main

if __name__ == "__main__":
    main()

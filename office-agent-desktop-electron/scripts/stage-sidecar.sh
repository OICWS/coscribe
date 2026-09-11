#!/usr/bin/env bash
# Builds coscribe-web's PyInstaller onedir sidecar and stages it into
# resources/sidecar/, where package.json's build.extraResources entry
# expects it (electron-builder's analog of Tauri's bundle.resources).
#
# Self-contained in this repo -- the office-agent-desktop (Tauri) shell
# this originally delegated to (see office-agent/ROADMAP.md's migration
# plan for why the Electron shell exists) isn't part of this repo, which
# only carries the Electron shell forward, not the Tauri one it replaced.
#
# PyInstaller does not cross-compile: run this on each target platform
# before building that platform's app -- a Linux-built sidecar cannot
# ship inside a Windows bundle.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ELECTRON_ROOT="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$ELECTRON_ROOT")"
OFFICE_AGENT="$REPO_ROOT/office-agent"

SIDECAR_DEST="$ELECTRON_ROOT/resources/sidecar"

# A Windows venv puts entry points under Scripts/, not bin/ -- this script
# runs under Git for Windows' bash (its own shebang assumes bash, and that's
# what GitHub's windows-latest runner and any real Windows dev box with Git
# installed both provide), so `uname`-style OS detection isn't reliable here;
# checking which layout actually exists on disk is.
if [ -x "$OFFICE_AGENT/.venv/Scripts/pyinstaller.exe" ]; then
  PYINSTALLER="$OFFICE_AGENT/.venv/Scripts/pyinstaller.exe"
else
  PYINSTALLER="$OFFICE_AGENT/.venv/bin/pyinstaller"
fi

echo "==> Building coscribe-server via PyInstaller (onedir)"
(
  cd "$OFFICE_AGENT/packaging"
  # Uses office-agent's own venv -- must already have pyinstaller installed
  # (`pip install pyinstaller`) alongside the project's real dependencies,
  # so the frozen build's import graph matches what's actually installed.
  "$PYINSTALLER" coscribe-server.spec --noconfirm
)

echo "==> Staging into $SIDECAR_DEST"
rm -rf "$SIDECAR_DEST"
mkdir -p "$(dirname "$SIDECAR_DEST")"
cp -R "$OFFICE_AGENT/packaging/dist/coscribe-server" "$SIDECAR_DEST"

echo "==> Done. $(du -sh "$SIDECAR_DEST" | cut -f1) staged."

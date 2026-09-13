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
#
# Always rebuilds the frontend first (below), rather than trusting
# whatever happens to already be sitting in src/coscribe/web/static/ --
# a real, costly live bug, not a hypothetical: a checkout that was
# several commits behind at the time of one `npm run build` (against
# the private repo) left a stale frontend baked into every sidecar
# built afterward, even once the branch itself was correctly updated to
# HEAD (nothing here or in git ties the build *output* to the source
# commit it came from). The symptom looked exactly like a native-
# embedding bug -- the stale frontend predated isElectron() detection
# working, so the Browser panel silently fell back to the legacy
# screencast implementation instead, produced CDP-relay errors, and
# cost a long real-hardware debugging session before the actual cause
# (stale static assets, not a runtime bug) was found. Rebuilding here
# every time this script runs is cheap compared to that.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ELECTRON_ROOT="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$ELECTRON_ROOT")"
OFFICE_AGENT="$REPO_ROOT/office-agent"

SIDECAR_DEST="$ELECTRON_ROOT/resources/sidecar"

echo "==> Building frontend (coscribe-web's static assets)"
(
  cd "$OFFICE_AGENT/frontend"
  npm install
  # Outputs directly into ../src/coscribe/web/static/ (see
  # frontend/vite.config.ts's own build.outDir) -- picked up below by
  # collect_data_files("coscribe") in coscribe-server.spec, no separate
  # copy step needed.
  npm run build
)

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

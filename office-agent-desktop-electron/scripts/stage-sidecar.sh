#!/usr/bin/env bash
# Thin wrapper around office-agent-desktop's stage-sidecar.sh (the Tauri
# shell being phased out -- see office-agent/ROADMAP.md's migration
# plan), reusing its PyInstaller build step instead of duplicating it.
# Stages into resources/sidecar/, where package.json's
# build.extraResources entry expects it (electron-builder's analog of
# Tauri's bundle.resources).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ELECTRON_ROOT="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$ELECTRON_ROOT")"

exec bash "$REPO_ROOT/office-agent-desktop/scripts/stage-sidecar.sh" "$ELECTRON_ROOT/resources/sidecar"

#!/bin/bash
set -euo pipefail

# Only needed for Claude Code on the web's remote sessions -- a local
# checkout already has its own dev environment set up by hand.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# office-agent (the "coscribe" package): Python deps, per its own
# README's documented setup (`python3 -m venv .venv && pip install -e
# ".[dev,web]"`). Idempotent: pip install -e is a fast no-op once the
# venv already has everything.
cd "$CLAUDE_PROJECT_DIR/office-agent"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -e ".[dev,web]"

# LibreOffice: this environment's base image ships only
# libreoffice-core/libreoffice-common, missing the actual application
# modules (impress/writer/calc) that provide the document-loading
# filters. Without them `soffice --headless --convert-to ...` fails on
# every input file, including a trivial one, with "Error: source file
# could not be loaded" -- confirmed via `strace`: the failure is
# `openat("/usr/lib/libreoffice/program/libswdlo.so", ...) = -1 ENOENT`
# (and the Impress/Calc equivalents), not a sandboxing or rendering-
# backend problem. This silently breaks write_pptx/write_docx/
# write_xlsx's own visual-QA step (overflow_warnings/preview
# generation), which degrades gracefully but was mistaken all session
# for "this environment can't render PPTX at all" before this was
# actually diagnosed. `apt list --installed` check keeps this cheap
# once already installed (the container's package cache persists).
if ! dpkg -s libreoffice-impress >/dev/null 2>&1; then
  apt-get install -y -qq libreoffice-impress libreoffice-writer libreoffice-calc
fi

# poppler-utils (pdftoppm): needed to render a PDF's pages as images for
# visual inspection (e.g. of a LibreOffice-converted .pptx/.docx) --
# without it, tools that rasterize PDF pages fail outright.
if ! dpkg -s poppler-utils >/dev/null 2>&1; then
  apt-get install -y -qq poppler-utils
fi

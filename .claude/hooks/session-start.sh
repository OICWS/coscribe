#!/bin/bash
set -euo pipefail

# Only needed for Claude Code on the web's remote sessions -- a local
# checkout already has its own dev environment set up by hand.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# The container's NO_PROXY lists pypi.org/files.pythonhosted.org/
# registry.npmjs.org, sending them direct, and the direct path answers
# 403 "Host not in allowlist" -- the same hosts are reachable through
# HTTPS_PROXY. Keep only loopback off the proxy for this script.
if [ -n "${HTTPS_PROXY:-}" ]; then
  export NO_PROXY="localhost,127.0.0.1,::1,127.0.0.0/8"
  export no_proxy="$NO_PROXY"
  export npm_config_noproxy="$NO_PROXY"
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
# A separate step for the reason pyproject.toml's google-genai comment gives.
.venv/bin/pip install -q -U "google-genai>=2.13.0,<2.14.0"

if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm ci --no-audit --no-fund)
fi
if ! command -v ocr >/dev/null 2>&1; then
  npm install -g @alibaba-group/open-code-review || true
fi

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
# actually diagnosed.
#
# poppler-utils (pdftoppm): needed to render a PDF's pages as images for
# visual inspection (e.g. of a LibreOffice-converted .pptx/.docx).
missing=()
for pkg in libreoffice-impress libreoffice-writer libreoffice-calc poppler-utils; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing+=("$pkg")
done
if [ ${#missing[@]} -gt 0 ]; then
  # The proxy only carries HTTPS (CONNECT); the image's Ubuntu sources
  # are plain http:// and get a 403, so fetch the same mirrors over https.
  if [ -n "${HTTPS_PROXY:-}" ]; then
    sed -i 's|http://archive.ubuntu.com|https://archive.ubuntu.com|; s|http://security.ubuntu.com|https://security.ubuntu.com|' \
      /etc/apt/sources.list.d/ubuntu.sources 2>/dev/null || true
    echo "Acquire::https::Proxy \"$HTTPS_PROXY\";" > /etc/apt/apt.conf.d/99ccrproxy
    apt-get update -qq
  fi
  apt-get install -y -qq "${missing[@]}"
fi

# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the bundled `coscribe-server` (desktop sidecar).

One-DIR bundle (exe + `_internal/` support folder) shipped via
coscribe-desktop's `extraResources` slot (Electron; the now-legacy Tauri
shell's equivalent was its own `resources` slot) -- deliberately NOT
`--onefile`. A onefile build self-extracts its whole archive to a temp
dir on EVERY launch (a real, measured problem in a comparable Tauri +
Python sidecar project this was modeled on: 6-7s of splash-screen delay
per launch, vs. ~0.5s for the actual Python import) -- which would erase
this project's own startup-latency work (see runtime_lg/README.md's
"coscribe-web-lg slow startup" sections: real, measured effort
brought cold import time down to ~1.1-1.4s). `--onedir` is not optional.

The wrinkles handled here, discovered by building this spec for real
against this project's actual dependency tree, not guessed:
  - langchain/langgraph's ecosystem (langchain-core/-anthropic/
    -google-genai/-openai, langgraph, langgraph-checkpoint-sqlite,
    langchain-mcp-adapters, langsmith) does a fair amount of dynamic
    provider/plugin lookup PyInstaller's static import analysis can't
    see through on its own -- collected explicitly below.
  - Several of this project's own tool modules (documents.py/
    spreadsheets.py/presentations.py) deliberately lazy-import their
    heavy libraries *inside function bodies*, not at module level (see
    those modules' own docstrings -- a real startup-time fix, this
    session). PyInstaller's static analysis walks module-level imports;
    a lazy import inside a function is exactly the kind of thing it can
    miss silently, so every one of those libraries (mammoth, pdfplumber,
    docx, markdownify, reportlab, openpyxl, pptx) is collected
    explicitly too -- confirmed necessary by actually building this spec
    and driving write_docx/write_xlsx/write_pptx/write_pdf through the
    frozen binary, not assumed.
  - uvicorn/fastapi/starlette load ASGI protocol/lifespan implementations
    dynamically -> collect_all.
  - certifi's CA bundle must ship for TLS (calling out to Gemini/
    Anthropic/OpenAI/DeepSeek/etc., and web_search's DuckDuckGo calls).

Cross-platform: paths are derived from this spec's own location
(SPECPATH), never hardcoded, so the same spec builds native binaries on
macOS, Windows, and Linux (PyInstaller itself does not cross-compile --
each platform's binary must be built on that platform). On Windows,
PyInstaller appends `.exe` to `name`. Built as a normal console app on
every OS -- a windowed (console=False) build leaves sys.stdout/stderr as
None, which breaks uvicorn's own startup logging and hangs the server.
To avoid a console window flashing in the desktop app, coscribe-
desktop's Electron shell spawns this sidecar with `windowsHide: true`
instead (see office-agent-desktop/src/main/sidecar.ts -- the now-legacy
Tauri shell used the equivalent Windows CREATE_NO_WINDOW flag before
it), which hides the window while keeping stdio intact.
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

# SPECPATH is injected by PyInstaller and points at this file's directory
# (<repo>/office-agent/packaging). Derive everything else from it -- no
# hardcoded paths.
PACKAGING = SPECPATH
ROOT = os.path.dirname(PACKAGING)

hiddenimports = []
datas = []
binaries = []

# This project's own package, plus its core runtime dependencies whose
# import graphs PyInstaller's static analysis can't fully see through on
# its own (dynamic provider/plugin lookup, docstring-based tool-schema
# generation).
for pkg in (
    "coscribe",
    "aisuite",
    "mcp",
    "ddgs",
    "docstring_parser",
    "langchain",
    "langchain_core",
    "langchain_anthropic",
    "langchain_google_genai",
    "langchain_openai",
    "langchain_mcp_adapters",
    "langgraph",
    # A separate pip distribution (langgraph-checkpoint-sqlite) merged into
    # langgraph's own namespace -- collect_submodules("langgraph") above
    # walks langgraph's own package directory, which may not include a
    # separately-installed namespace-package contributor, so this is listed
    # explicitly rather than assumed to already be covered.
    "langgraph.checkpoint.sqlite",
    "langsmith",
):
    hiddenimports += collect_submodules(pkg)

# This project's own tool modules deliberately lazy-import these inside
# function bodies (a real startup-time fix, not an oversight -- see
# tools/documents.py's, tools/spreadsheets.py's, and
# tools/presentations.py's own docstrings) -- PyInstaller's static
# analysis walks module-level imports and can miss a function-local one,
# so every one of these needs to be collected explicitly, the same as
# uvicorn/certifi/anyio need collect_all for their own dynamic loading.
for pkg in (
    "uvicorn",
    "fastapi",
    "starlette",
    "certifi",
    "anyio",
    "websockets",
    "mammoth",
    "pdfplumber",
    "docx",
    "markdownify",
    "reportlab",
    "openpyxl",
    "pptx",
):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# The frontend itself (web/static/*.html|js|css) -- not Python modules,
# so collect_submodules("coscribe") above never sees it. Confirmed
# necessary by actually running the frozen binary and hitting a real
# RuntimeError from Starlette's StaticFiles mount ("Directory ... does
# not exist") without this.
datas += collect_data_files("coscribe")

a = Analysis(
    [os.path.join(PACKAGING, "server_entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PySide6", "PyQt5"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="coscribe-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # Console on every OS: a windowed build nulls stdout/stderr and hangs
    # uvicorn. office-agent-desktop hides the window on Windows via
    # CREATE_NO_WINDOW when spawning the sidecar instead.
    console=True,
    # target_arch left unset -> PyInstaller builds for the host architecture.
)
# Onedir: dist/coscribe-server/{coscribe-server[.exe], _internal/}.
# office-agent-desktop's build scripts (stage-sidecar.sh) stage this
# whole folder into resources/sidecar/ for Electron's `extraResources`
# bundling (the now-legacy Tauri shell staged the equivalent into
# src-tauri/binaries/sidecar/ for its own `resources` bundling).
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="coscribe-server",
)

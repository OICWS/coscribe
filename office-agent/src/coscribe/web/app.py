"""FastAPI app: serves the chat WebSocket protocol and the static frontend.

Built on runtime_lg (the LangGraph-based runtime -- see runtime_lg/README.md
for the full migration history). This module was `web/app_lg.py` through
Phase 3 of that migration, developed alongside the original hand-rolled-
runtime `web/app.py` as a parallel track; that original was deleted and
this one promoted in its place once runtime_lg reached real feature parity
and several rounds of live use turned up no gaps the old runtime still
covered (see runtime_lg/README.md's "web cutover" section).

Reuses the existing frontend (web/static/*) unmodified -- the WS wire
protocol was kept identical to the old runtime's on purpose throughout the
migration, see runtime_lg/README.md's research notes. Plan Mode/Accept-
Edits/Compact/Hooks are wired in via web/session.py -- see that module's
docstring and runtime_lg/README.md for the design (interrupt_on membership
doubling as the plan-mode-blocked set; all-tools interrupt gating when
PreToolUse hooks are configured; checkpointer-state compaction). MCP tools
go through runtime_lg/mcp.py's connect_mcp_tools_lg (langchain-mcp-
adapters), not tools/connect_mcp_tools (aisuite) -- see that module's
docstring for why.

One real behavioral gap vs. the old runtime, **now closed for MCP
connectors specifically, still open for everything else**: config changes
elsewhere (a new custom provider, a new/removed skill) still only take
effect for the next new thread/session, not every
already-open one. The old runtime's identical endpoints hot-reloaded into
every live ChatSession because runtime/runner.py's Runner re-read
agent.tools and re-resolved the model fresh on every single turn;
runtime_lg's create_agent() compiles a fixed graph once (model and tools
baked in), so an already-open ChatSession keeps whatever it was built with
until its process restarts or a fresh thread_id is opened, unless
something explicitly rebuilds that graph in place.

MCP connectors got that explicit rebuild after a real, live-reported bug:
a user enabled a connector mid-conversation and it had no effect on that
same, already-open thread (confirmed against this exact "next new session
only" gap). add_mcp_server/remove_mcp_server/bump_mcp_server_version below
now all call _refresh_all_sessions_extra_tools() after mutating
extra_tools_holder, which calls ChatSessionLG.refresh_extra_tools on every
currently-open session -- same "rebuild the compiled graph in place, same
checkpointer/thread_id" mechanism switch_model already used for models.
Providers/skills don't get the same treatment here: rebuilding
*every* open session's graph on *every* kind of config change is
meaningfully more work and risk than closing the one gap a real user
actually hit, so those remain "persists correctly, next session picks it
up," a decision made explicitly, not an oversight.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from dotenv import dotenv_values, load_dotenv, set_key
from fastapi import FastAPI, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import BaseModel

from .. import __version__
from ..cli import _dotenv_path, _load_settings_or_none
from ..config import Settings
from ..coordinator import build_coordinator_agent
from ..runtime import (
    LLMClient,
    delete_secret,
    empty_hooks_config,
    env_delete_secret_if_ref,
    env_resolve_secret_for_display,
    env_value_for_storage,
    harden_file_permissions,
    load_custom_providers,
    load_hooks_config,
    resolve_env_keyring_refs,
    resolve_secret,
    run_hook,
    store_secret,
)
from ..runtime.types import get_tool_metadata
from ..runtime_lg import (
    extract_text,
    poll_due_scheduled_tasks,
    poll_due_wakes,
    strip_mode_note,
)
from ..tools import (
    SkillInfo,
    SkillUploadError,
    load_builtin_skills,
    load_skills,
    save_uploaded_skill,
)
from ..tools._workspace import WorkspaceScope
from ..tools.mcp import load_mcp_server_configs, validate_mcp_config
from ..tools.memory import load_memory
from ..tools.node_env import install_package as install_node_package
from ..tools.node_env import list_packages as list_node_packages
from ..tools.node_env import uninstall_package as uninstall_node_package
from ..tools.scheduled_tasks import ScheduledTriggerStore, compute_next_run_at, create_trigger
from ..tools.script_env import (
    fallbacks_for_platform,
    get_interpreter_override,
    install_package,
    list_packages,
    set_interpreter_override,
    uninstall_package,
    working_interpreters,
)
from ..tools.tasks import TaskToolkit
from ..tools.workflows import WorkflowRunStore, WorkflowStore, reconcile_interrupted_runs
from .background_events import BackgroundEvent, BackgroundEventBus
from .browser_detect import find_windows_browser
from .browser_panel import BrowserPanelError, BrowserPanelSession
from .session import ChatSessionLG

if TYPE_CHECKING:
    # Real type only needed for a local variable annotation below (never
    # evaluated at runtime -- `from __future__ import annotations` is in
    # effect) -- kept out of the real import graph so `import coscribe.
    # web.app` doesn't drag in langchain_mcp_adapters/mcp for a user with
    # no MCP servers configured. See connect_mcp_tools_lg/connect_one_mcp_
    # server_lg below for the same reasoning applied to the functions that
    # actually need this module at runtime.
    from ..runtime_lg.mcp import McpServerConnection

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # office docs/PDFs, not video files
_PREVIEW_NAME_RE = re.compile(r"[0-9a-f]{32}\.png")  # tools/_thumbnail.py's uuid4().hex naming
# How long lifespan() blocks app startup on connect_mcp_tools_lg before
# letting a still-connecting server finish in the background instead --
# see lifespan's own comment. Same default Claude Code itself settled on
# (MCP_CONNECT_TIMEOUT_MS) for the identical problem.
MCP_STARTUP_TIMEOUT_SECONDS = 5.0


def _unique_upload_path(scope: WorkspaceScope, filename: str) -> Path:
    name = Path(filename).name or "upload"
    candidate = scope.resolve(name)
    if not candidate.exists():
        return candidate
    stem, suffix, counter = candidate.stem, candidate.suffix, 1
    while True:
        candidate = scope.resolve(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


STATIC_DIR = Path(__file__).parent / "static"


class _NoCacheStaticFiles(StaticFiles):
    """Plain StaticFiles lets browsers cache app.js/style.css/index.html
    with only heuristic (best-effort, not guaranteed-fresh) revalidation --
    fine for a CDN-fronted public site, but this is a single local process
    whose static assets change on every `git pull` + restart, with nothing
    else forcing a refetch (no hashed filenames, no version query param).
    A stale-cached app.js after a redeploy is worse than it sounds: this
    script is one flat top-to-bottom file, so a single DOM id it references
    that a newer index.html has moved or removed throws partway through and
    silently kills every listener registered after that point -- which
    looks exactly like "nothing responds to clicks" with no visible error.
    """

    def file_response(self, *args: Any, **kwargs: Any) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-store"
        return response


# A small, curated, hardcoded list -- not a live marketplace. Every entry
# is a stdio/local-command MCP server, matching tools/mcp.py's existing
# schema.
MCP_CATALOG: list[dict[str, Any]] = [
    {
        "name": "playwright",
        "description": "Browser automation -- navigate, click, fill forms, take screenshots.",
        "command": "npx",
        # Pinned, not "@latest" -- an unpinned tag makes npx hit the npm
        # registry to check for a newer version on every single startup,
        # which is most of the extra delay users notice adding this one.
        # Bump by hand occasionally (`npm view @playwright/mcp version`),
        # or use the Connectors panel's "Check for updates" on the
        # configured entry once it's added -- that's wired to bump this
        # same "package@version" arg live, no restart needed.
        "args": ["@playwright/mcp@0.0.78"],
        # Needs a real Chromium-family browser binary to drive -- the
        # frontend checks for one before adding this specific entry (see
        # /api/mcp/browser-check), not a generic flag every catalog entry
        # needs.
        "needs_browser_check": True,
    },
    {
        "name": "fetch",
        "description": "Fetch and read the text content of web pages.",
        "command": "uvx",
        # `--with "mcp<2.0.0"` pins a compatible mcp SDK version -- a real,
        # live-reported bug: mcp-server-fetch's own declared dependency is
        # just `mcp>=1.1.3`, no upper bound, so a bare `uvx mcp-server-
        # fetch` resolves the newest published mcp (2.0.0 as of this
        # writing), which renamed `McpError` to `MCPError`.
        # mcp-server-fetch still imports the old name, so every connection
        # attempt crashed immediately with `ImportError: cannot import
        # name 'McpError'` inside the child process (surfaced to us only
        # as an opaque "Connection closed" from the MCP handshake, not the
        # real traceback) -- confirmed by reproducing the bare `uvx
        # mcp-server-fetch` failure directly and finding this exact upper-
        # bound gap in mcp-server-fetch's own published metadata. Not a
        # coscribe bug; this pin works around it until upstream either
        # updates mcp-server-fetch or mcp-server-fetch adds its own upper
        # bound.
        "args": ["--with", "mcp<2.0.0", "mcp-server-fetch"],
    },
    {
        "name": "memory",
        "description": "A persistent knowledge-graph memory store (entities, relations, "
        "observations) -- separate from coscribe's own MEMORY.md file.",
        "command": "npx",
        "args": ["@modelcontextprotocol/server-memory@2026.7.4"],
    },
    {
        "name": "sequential-thinking",
        "description": "A structured step-by-step reasoning scaffold for working through "
        "complex, multi-step problems.",
        "command": "npx",
        "args": ["@modelcontextprotocol/server-sequential-thinking@2026.7.4"],
    },
    {
        "name": "time",
        "description": "Current time and timezone conversions.",
        "command": "uvx",
        # Same upstream mcp<2.0.0 incompatibility as the fetch entry above
        # -- confirmed live, identical ImportError from mcp-server-time's
        # own unpinned mcp>=... dependency.
        "args": ["--with", "mcp<2.0.0", "mcp-server-time"],
    },
    {
        "name": "slack",
        "description": "Post messages, read channels and threads, react to messages -- "
        "needs a Slack app you create yourself (a few minutes on api.slack.com) and "
        "its Bot User OAuth Token, not a coscribe-hosted sign-in.",
        "command": "npx",
        # Official @modelcontextprotocol server, same publisher as the
        # memory/sequential-thinking entries above. Deliberately *not* the
        # "click, get redirected to Slack's login page" OAuth UX ROADMAP.md's
        # Phase 5a originally sketched -- checked this package's own setup
        # docs first: it authenticates with a plain Bot User OAuth Token
        # (`xoxb-...`) the user copies from their own Slack app's "OAuth &
        # Permissions" page after clicking "Install to Workspace" there,
        # the exact same shape the old (removed) GitHub PAT catalog entry
        # used -- there's no token exchange for coscribe to broker, so
        # there's nothing for a loopback-redirect flow to actually buy
        # here. needs_config reuses that same still-live Custom-tab
        # prefill mechanism (see ConnectorsTab.tsx's prefillCustomForm) --
        # not new code, and not the removed GitHub entry's device-flow
        # implementation either (see the comment below).
        "args": ["@modelcontextprotocol/server-slack@2025.4.25"],
        "env": {"SLACK_BOT_TOKEN": "", "SLACK_TEAM_ID": ""},
        "needs_config": True,
    },
    {
        "name": "office365",
        "description": "Outlook mail and calendar, OneDrive files, Excel, OneNote, "
        "To Do, Planner -- signs in through its own device-code flow (the agent "
        "gives you a URL and a code to enter, no setup beforehand).",
        "command": "npx",
        # Community server (github.com/Softeria/ms-365-mcp-server), not an
        # @modelcontextprotocol/ package -- included anyway (unlike the
        # Postgres/Filesystem servers deliberately left out below) on real
        # health signals: MIT-licensed, 288 published versions, built on
        # Microsoft's own @azure/msal-node rather than a hand-rolled OAuth
        # client. A genuinely better fit than it first looks: unlike
        # Slack/Notion (paste a token) or Google Drive (Cloud Console app
        # registration + OAuth client JSON), this one ships its own
        # pre-registered Microsoft app and authenticates via MSAL's Device
        # Code flow *entirely inside its own MCP tools* (`login`/`verify-
        # login`) -- no coscribe-side OAuth mechanism, no Settings-tab
        # config, not even needs_config: the agent calls `login`, shows the
        # user a URL+code, done. The exact same flow shape as the removed
        # GitHub catalog entry's device flow, just handled by the server
        # itself instead of by coscribe. Personal-account tool set only
        # (no `--org-mode`) -- Teams/SharePoint need a work/school account
        # and a different invocation; addable by hand via the Custom tab
        # for anyone who specifically wants that.
        "args": ["@softeria/ms-365-mcp-server@0.148.2"],
    },
]

# git/github catalog entries (local git ops via mcp-server-git; GitHub
# issues/PRs/repo search via GitHub's own remote MCP server + an OAuth
# Device Flow sign-in) existed here and were removed -- coscribe's target
# user is a general office file/task automation assistant, not a developer
# tool, and neither is relevant to that audience by default. Still
# addable by hand via the Connectors panel's Custom tab (name + command +
# args) for anyone who specifically wants them; only the curated,
# one-click catalog entries were removed, along with the GitHub-specific
# OAuth Device Flow implementation that entry was the only caller of (see
# git history for `web/github_oauth.py` if that flow is ever needed again
# for a different provider -- the slack entry above does *not* revive or
# reuse it, see that entry's own comment for why a token-exchange flow
# isn't actually needed there).

# Well-maintained official MCP servers that exist but are deliberately left
# out of MCP_CATALOG above -- these don't just need a path/token filled in
# via the Custom-tab prefill flow, they either overlap with an existing
# coscribe capability or raise a maintenance concern:
#   - Filesystem (npm `@modelcontextprotocol/server-filesystem`) -- overlaps
#     with coscribe's own workspace-scoped file tools
#   - Postgres (npm `@modelcontextprotocol/server-postgres`) -- its latest
#     release (0.6.2, not this repo's 2026.x calendar-versioned releases)
#     suggests it's seen less recent maintenance than the others above
# Both are still addable today via the Connectors panel's Custom tab (name +
# command + args + env) for anyone who wants them anyway.

# Convenience pre-fills for a few well-known OpenAI-compatible providers --
# not an exhaustive list. Any OpenAI-compatible endpoint works via the
# "Add custom provider" form below; these just save typing the base_url.
# Deliberately NO "default_model" guess here (unlike the builtin anthropic/
# gemini entries in get_providers_catalog below, whose model IDs this
# project's own release cadence controls) -- a third-party vendor's model
# lineup is entirely outside this project's control and turns over on its
# own schedule (real example: this catalog previously hardcoded DeepSeek's
# default_model as "deepseek-v4-flash", which was already wrong -- live
# user report named the real current models as "deepseek-flash"/
# "deepseek-v4-pro"). A wrong guessed model name silently pre-filled into
# the Providers tab's form is worse than an empty field the user has to
# fill in themselves from the vendor's own current docs: it looks
# authoritative but isn't, and a user who doesn't second-guess it gets a
# confusing "model not found"-shaped failure instead of an obviously-
# blank field asking for input. base_url is different -- an API host
# essentially never changes, so that part of each entry below is still a
# real, low-risk time-saver, verified against each vendor's docs.
PROVIDER_CATALOG = [
    {
        "name": "deepseek",
        "description": "DeepSeek's OpenAI-compatible API.",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "",
    },
    {
        "name": "kimi",
        "description": "Moonshot AI's Kimi, OpenAI-compatible API.",
        "base_url": "https://api.moonshot.ai/v1",
        "default_model": "",
    },
    {
        "name": "glm",
        "description": "Zhipu's GLM, OpenAI-compatible API.",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "default_model": "",
    },
    {
        "name": "ollama",
        # add_provider (below) still requires a non-blank API key for every
        # custom provider, gated tools included -- Ollama's own OpenAI-
        # compatible server never actually checks the Authorization header,
        # so any placeholder text works; called out explicitly here since
        # it's the one entry in this catalog where the field means nothing
        # to the provider it's configuring, unlike a real cloud API key.
        "description": (
            "Ollama's local OpenAI-compatible API -- fully offline, no cloud "
            "account. API key can be any placeholder text (e.g. \"ollama\"), "
            "it isn't actually checked. Fill in Default Model yourself with "
            "whatever you've already pulled via `ollama pull <model>` -- "
            "coscribe can't see what's installed, so there's no safe guess "
            "to pre-fill here."
        ),
        "base_url": "http://localhost:11434/v1",
        "default_model": "",
    },
]

# Built-in providers: each has a raw (unprefixed) API-key env var -- read
# directly by each provider SDK, not modeled on Settings (see config.py's
# Settings docstring); values are never sent to the browser in full, only
# masked -- and an *optional*, COSCRIBE_-prefixed "default model"
# companion. The companion is deliberately COSCRIBE_-prefixed, unlike
# the key itself: no provider SDK reads
# "COSCRIBE_ANTHROPIC_DEFAULT_MODEL" -- it's pure coscribe
# bookkeeping (which model to offer as this provider's quick-pick in the
# model-picker / Providers tab), exactly what that prefix is used for
# everywhere else. Deliberately NOT added to COSCRIBE_ENV_VARS/Settings:
# get_config/get_providers below read it fresh from dotenv_values(".env")
# on every request, so (unlike every COSCRIBE_ENV_VARS entry) it never
# needs a restart to take effect -- see update_config's restart_required
# computation.
BUILTIN_PROVIDERS: list[dict[str, str]] = [
    {
        "key": "anthropic",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model_env": "COSCRIBE_ANTHROPIC_DEFAULT_MODEL",
    },
    {
        "key": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "default_model_env": "COSCRIBE_OPENAI_DEFAULT_MODEL",
    },
    {
        "key": "gemini",
        "api_key_env": "GEMINI_API_KEY",
        "default_model_env": "COSCRIBE_GEMINI_DEFAULT_MODEL",
    },
]

PROVIDER_KEY_ENV_VARS = [p["api_key_env"] for p in BUILTIN_PROVIDERS]
PROVIDER_DEFAULT_MODEL_ENV_VARS = [p["default_model_env"] for p in BUILTIN_PROVIDERS]

# Example models shown as placeholder text on the setup page below -- the
# same three .env.example already documents in its own COSCRIBE_DEFAULT_MODEL
# comment, kept in one place so they can't quietly drift apart.
_PROVIDER_EXAMPLE_MODELS = {
    "anthropic": "claude-sonnet-4-5-20250929",
    "openai": "gpt-4o",
    "gemini": "gemini-2.5-flash",
}

# One-line description shown under the provider dropdown on the setup
# page below -- purely explanatory copy, not a link to get a key (a
# generic "how do I get an API key" link was considered and deliberately
# left out; this page assumes the user already has one).
_PROVIDER_DESCRIPTIONS = {
    "anthropic": "Claude models from Anthropic.",
    "openai": "GPT models from OpenAI.",
    "gemini": "Gemini models from Google.",
}

# Self-contained (no build step, no external font/asset fetch -- this is
# the one page in this app that has to work before the built frontend's
# own bundle is guaranteed to mean anything) first-run setup form, served
# by create_setup_app below in place of the real app. Visually matches
# office-agent-desktop's own splash page (dist/index.html) -- same CSS
# variables, system-font stack -- since the two are effectively siblings:
# the splash page IS how a fresh desktop install reaches this page (it
# polls the sidecar's origin and redirects the moment *anything* answers,
# setup page included), and the failure state it used to show for exactly
# this situation ("coscribe couldn't start... check the log") is what this
# page replaces. #coscribe-setup-marker is a plain marker element the
# page's own script below looks for after reconnecting post-submit, to
# tell "still this page" apart from "the real app now" without depending
# on response content otherwise.
_SETUP_PAGE_HTML = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Set up coscribe</title>
    <style>
      :root {
        color-scheme: light dark;
        --bg: #f3f2f2;
        --card-bg: #f8f4f4;
        --fg: #201e1d;
        --muted: #7d7979;
        --border: rgba(32, 30, 29, 0.16);
        --accent: #ec3013;
        --accent-fg: #f3f2f2;
        --danger: #dd2b0f;
      }
      @media (prefers-color-scheme: dark) {
        :root {
          --bg: #201e1d;
          --card-bg: #2d2b2b;
          --fg: #f3f2f2;
          --muted: #a39f9e;
          --border: rgba(243, 242, 242, 0.16);
          --accent: #ff563c;
          --accent-fg: #201e1d;
          --danger: #ff9783;
        }
      }
      * { box-sizing: border-box; }
      html, body { height: 100%; margin: 0; }
      body {
        display: flex;
        align-items: center;
        justify-content: center;
        background: var(--bg);
        color: var(--fg);
        font-family: -apple-system, "Segoe UI", system-ui, sans-serif;
      }
      main {
        width: 400px;
        display: flex;
        flex-direction: column;
        gap: 16px;
        padding: 28px;
        border-radius: 14px;
        background: var(--card-bg);
        border: 1px solid var(--border);
      }
      .brand { display: flex; align-items: center; gap: 10px; }
      .mark {
        width: 30px;
        height: 30px;
        flex-shrink: 0;
        border-radius: 8px;
        background: var(--accent);
        color: var(--accent-fg);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 15px;
        font-weight: 700;
      }
      h1 { font-size: 17px; margin: 0; }
      p.lede { font-size: 13px; color: var(--muted); margin: 0; line-height: 1.5; }
      .group { display: flex; flex-direction: column; gap: 8px; }
      .step {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: 12px;
        font-weight: 600;
        color: var(--muted);
      }
      .step .num {
        width: 16px;
        height: 16px;
        flex-shrink: 0;
        border-radius: 50%;
        background: var(--border);
        color: var(--fg);
        display: flex;
        align-items: center;
        justify-content: center;
        font-size: 10px;
      }
      label { font-size: 12px; color: var(--muted); display: block; margin-bottom: 4px; }
      select, input {
        width: 100%;
        font-size: 13px;
        font-family: inherit;
        padding: 7px 9px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--bg);
        color: var(--fg);
      }
      .provider-desc, .hint {
        font-size: 11px;
        color: var(--muted);
        margin: 5px 2px 0;
        line-height: 1.4;
      }
      button {
        margin-top: 6px;
        width: 100%;
        font-size: 13px;
        font-weight: 600;
        font-family: inherit;
        padding: 9px;
        border-radius: 8px;
        border: none;
        background: var(--accent);
        color: var(--accent-fg);
        cursor: pointer;
      }
      button:disabled { opacity: 0.6; cursor: default; }
      .status { font-size: 12px; min-height: 1.2em; text-align: center; }
      .status.error { color: var(--danger); }
      .status.ok { color: var(--muted); }
    </style>
  </head>
  <body>
    <main id="coscribe-setup-marker">
      <div class="brand">
        <div class="mark">c</div>
        <h1>Welcome to coscribe</h1>
      </div>
      <p class="lede">
        A local office assistant that reads and writes real Word, Excel,
        and PowerPoint files. It needs one AI provider to work with --
        pick one below and paste the API key you already have for it.
        This is written to your local <code>.env</code>, never sent
        anywhere but that provider, and you can add more providers or
        change this at any time from Settings &rarr; Providers.
      </p>

      <div class="group">
        <div class="step"><span class="num">1</span>Choose a provider</div>
        <div class="field">
          <label for="provider">Provider</label>
          <select id="provider">
            <option value="anthropic">Anthropic (Claude)</option>
            <option value="openai">OpenAI</option>
            <option value="gemini">Gemini</option>
          </select>
          <p class="provider-desc" id="providerDesc"></p>
        </div>
        <div class="field">
          <label for="model">Model</label>
          <input id="model" type="text" autocomplete="off" />
          <p class="hint">
            Prefilled with a good default -- change it if you'd rather use a different one.
          </p>
        </div>
      </div>

      <div class="group">
        <div class="step"><span class="num">2</span>Add your API key</div>
        <div class="field">
          <label for="apiKey">API key</label>
          <input id="apiKey" type="password" autocomplete="off" />
        </div>
      </div>

      <button id="submit" type="button">Save and start</button>
      <div class="status" id="status"></div>
    </main>
    <script>
      const EXAMPLES = __PROVIDER_EXAMPLE_MODELS_JSON__;
      const DESCRIPTIONS = __PROVIDER_DESCRIPTIONS_JSON__;
      const providerEl = document.getElementById("provider");
      const modelEl = document.getElementById("model");
      const providerDescEl = document.getElementById("providerDesc");
      const apiKeyEl = document.getElementById("apiKey");
      const submitEl = document.getElementById("submit");
      const statusEl = document.getElementById("status");

      // The model field starts prefilled with a sensible default so
      // "pick a provider, paste a key" is enough to finish setup -- but
      // only until the user actually types into it themselves, after
      // which switching providers stops overwriting their own choice.
      let modelTouched = false;
      modelEl.addEventListener("input", () => { modelTouched = true; });

      function applyProvider() {
        providerDescEl.textContent = DESCRIPTIONS[providerEl.value] || "";
        if (!modelTouched) modelEl.value = EXAMPLES[providerEl.value] || "";
      }
      providerEl.addEventListener("change", applyProvider);
      applyProvider();

      function setStatus(text, kind) {
        statusEl.textContent = text;
        statusEl.className = "status" + (kind ? " " + kind : "");
      }

      // The brief window between this page's own submit handler stopping
      // the setup server and main()'s _run_web_server starting the real
      // one on the same port -- both servers bind the identical host:port
      // sequentially, never at once, so a connection-refused blip here is
      // expected, not a real failure. Same no-cors liveness-probe trick
      // office-agent-desktop's own splash page (dist/index.html) already
      // uses for the identical "is anything listening yet" question.
      async function waitForRealAppAndReload() {
        for (let i = 0; i < 200; i++) {
          await new Promise((r) => setTimeout(r, 300));
          try {
            await fetch("/", { mode: "no-cors", cache: "no-store" });
            location.reload();
            return;
          } catch {
            // still down mid-handover -- keep polling.
          }
        }
        setStatus("Saved, but starting is taking unusually long -- try reloading.", "error");
      }

      async function submit() {
        const provider = providerEl.value;
        const model = modelEl.value.trim();
        const apiKey = apiKeyEl.value.trim();
        if (!model) { setStatus("Model cannot be blank.", "error"); return; }
        if (!apiKey) { setStatus("API key cannot be blank.", "error"); return; }

        submitEl.disabled = true;
        setStatus("Saving\\u2026", "ok");
        let result;
        try {
          const resp = await fetch("/api/setup", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ provider, model, api_key: apiKey }),
          });
          result = await resp.json();
        } catch {
          setStatus("Couldn't reach coscribe -- try again.", "error");
          submitEl.disabled = false;
          return;
        }
        if (!result.success) {
          setStatus(result.error || "Something went wrong.", "error");
          submitEl.disabled = false;
          return;
        }
        setStatus("Saved -- starting coscribe\\u2026", "ok");
        waitForRealAppAndReload();
      }

      submitEl.addEventListener("click", submit);
    </script>
  </body>
</html>
"""
_SETUP_PAGE_HTML = _SETUP_PAGE_HTML.replace(
    "__PROVIDER_EXAMPLE_MODELS_JSON__", json.dumps(_PROVIDER_EXAMPLE_MODELS)
).replace("__PROVIDER_DESCRIPTIONS_JSON__", json.dumps(_PROVIDER_DESCRIPTIONS))


class SetupRequest(BaseModel):
    provider: str
    model: str
    api_key: str


def create_setup_app(configured: asyncio.Future[Settings]) -> FastAPI:
    """First-run configuration app -- served by main()'s _run_web_server
    in place of the real app whenever _load_settings_or_none() (cli.py)
    comes back empty, i.e. no usable COSCRIBE_DEFAULT_MODEL/API key yet.
    That used to mean the whole process printing a "Configuration error"
    and exiting (cli.py's _load_settings) the instant it was reached --
    survivable at a terminal (fix .env, rerun), a dead end for the
    desktop app (office-agent-desktop/dist/index.html's splash page has
    no way to tell this apart from any other startup crash, so it just
    showed "coscribe couldn't start" and pointed at a log file).

    Deliberately its own tiny FastAPI app, not a route bolted onto
    create_app_lg: that function assumes a valid Settings up front (it's
    a constructor parameter, not optional), and threading "settings might
    not exist yet" through everything it wires up (sessions dict,
    checkpointer path, tool registration, ...) for the sake of one setup
    screen isn't worth it. `configured` is how this hands control back:
    resolving it is the only thing that ends this app's own tenure on the
    port -- _run_web_server awaits it, then stops this server and starts
    the real one, in-process, same PID, same port. Deliberately not a
    process restart (os.execv or similar): confirmed that's unsafe here --
    Python's os.execv is a true in-place replacement on POSIX but the
    Windows CRT only emulates it as spawn-then-exit-the-original, which
    would change the PID office-agent-desktop's Rust side is tracking for
    kill-on-quit, breaking exactly the platform this ships on.
    """
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _SETUP_PAGE_HTML

    @app.post("/api/setup")
    async def setup(payload: SetupRequest) -> dict[str, Any]:
        if configured.done():
            # Only reachable if the page's own submit button somehow
            # fires twice -- it disables itself immediately on click, and
            # _run_web_server tears this whole app down the moment
            # `configured` resolves, so there's no live route left to
            # double-submit in the ordinary case.
            return {"success": False, "error": "Already configured."}

        provider_key = payload.provider.strip().lower()
        builtin = next((p for p in BUILTIN_PROVIDERS if p["key"] == provider_key), None)
        if builtin is None:
            return {"success": False, "error": f"Unknown provider {payload.provider!r}."}
        model = payload.model.strip()
        if not model:
            return {"success": False, "error": "Model cannot be blank."}
        api_key = payload.api_key.strip()
        if not api_key:
            return {"success": False, "error": "API key cannot be blank."}

        # Same shape add_provider (below, for the already-running-app
        # case) writes -- a builtin provider's key stored via
        # env_value_for_storage (keyring ref when available, hardened-
        # permission plaintext fallback otherwise), plus
        # COSCRIBE_DEFAULT_MODEL so the very next _load_settings_or_none
        # call (right below) actually succeeds.
        dotenv_path = _dotenv_path()
        set_key(
            str(dotenv_path),
            builtin["api_key_env"],
            env_value_for_storage(f"builtin-provider:{builtin['api_key_env']}", api_key),
        )
        set_key(str(dotenv_path), "COSCRIBE_DEFAULT_MODEL", f"{provider_key}:{model}")
        harden_file_permissions(dotenv_path)

        settings = _load_settings_or_none()
        if settings is None:
            return {
                "success": False,
                "error": "Saved, but coscribe still couldn't start -- double-check the model name.",
            }
        configured.set_result(settings)
        return {"success": True}

    return app


# COSCRIBE_-prefixed Settings fields the panel exposes for editing.
# state_dir is deliberately omitted -- internal bookkeeping, not something
# a user needs to reach for.
COSCRIBE_ENV_VARS = [
    "COSCRIBE_DEFAULT_MODEL",
    "COSCRIBE_WORKSPACE_ROOT",
    "COSCRIBE_SKILLS_DIR",
    "COSCRIBE_MEMORY_PATH",
    "COSCRIBE_MCP_CONFIG_PATH",
    "COSCRIBE_PROVIDERS_CONFIG_PATH",
    "COSCRIBE_HOOKS_CONFIG_PATH",
    "COSCRIBE_LOG_LEVEL",
    "COSCRIBE_EXTRA_READABLE_DIRS",
    "COSCRIBE_EXTRA_WRITABLE_DIRS",
    "COSCRIBE_MAX_TURNS",
]

# Desktop-shell-consumed, not Settings-backed (see office-agent-desktop's
# sidecar.ts's shouldKeepRunningInBackground(), which reads this same
# .env file directly -- this Python process never branches on it; the
# now-legacy Tauri shell's own src-tauri/src/lib.rs did the equivalent
# before the Electron migration) -- so it's a separate list from
# COSCRIBE_ENV_VARS above for the same reason
# PROVIDER_DEFAULT_MODEL_ENV_VARS already is: update_config's own
# restart_required computation is keyed off COSCRIBE_ENV_VARS membership,
# and this one needs no coscribe-web restart to take effect (the desktop
# shell just re-reads the file at the next window-close, live).
DESKTOP_ENV_VARS = ["COSCRIBE_BACKGROUND_ON_CLOSE"]

# Blank is a silent footgun for these -- Path("") resolves to Path("."),
# and blank COSCRIBE_DEFAULT_MODEL/COSCRIBE_LOG_LEVEL make Settings()
# construction (default_model) or logging.basicConfig (log_level) raise
# outright on next startup. COSCRIBE_MCP_CONFIG_PATH/HOOKS_CONFIG_PATH
# are deliberately excluded -- config.py's Settings already treats a blank
# string there as "unset" gracefully -- and so are provider API keys, where
# blank just means "not configured," discovered at use time, not startup.
BLANK_UNSAFE_ENV_VARS = {
    "COSCRIBE_DEFAULT_MODEL",
    "COSCRIBE_WORKSPACE_ROOT",
    "COSCRIBE_SKILLS_DIR",
    "COSCRIBE_MEMORY_PATH",
    "COSCRIBE_LOG_LEVEL",
    "COSCRIBE_MAX_TURNS",
}


def _mask(value: str) -> str:
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


class ConfigUpdate(BaseModel):
    updates: dict[str, str]


class MemoryUpdate(BaseModel):
    content: str


class MCPServerUpdate(BaseModel):
    name: str
    # Exactly one of command (local, stdio) or server_url (remote,
    # streamable_http) -- validate_mcp_config enforces the XOR, this model
    # just carries both possible shapes. headers is server_url's
    # equivalent of env, masked/secret-stored the same way on the read/
    # write paths below.
    command: str | None = None
    args: list[str] = []
    env: dict[str, str] = {}
    server_url: str | None = None
    headers: dict[str, str] = {}


class MCPVersionBump(BaseModel):
    package: str
    version: str


class ThreadRename(BaseModel):
    title: str


class ScheduledTaskCreate(BaseModel):
    name: str
    kind: str
    at: str
    prompt: str | None = None
    workflow_name: str | None = None
    weekday: int | None = None
    day_of_month: int | None = None


# Plain name ("mcp-server-fetch") or scoped ("@playwright/mcp") npm package
# name -- deliberately doesn't allow anything npm view could misparse as a
# flag (e.g. a leading "-"), since `package` here comes straight from the
# browser.
_NPM_PACKAGE_NAME_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")


class ProviderUpdate(BaseModel):
    name: str
    # Blank for a built-in (anthropic/openai/gemini) -- their SDKs don't take
    # one, only custom OpenAI-compatible providers need it.
    base_url: str = ""
    api_key: str
    default_model: str = ""


class ScriptEnvPackageInstall(BaseModel):
    package: str


class ScriptEnvInterpreterUpdate(BaseModel):
    path: str  # blank clears the override, reverting to auto-detection


def _read_mcp_servers_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"mcpServers": {}}
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw.get("mcpServers"), dict):
        raw["mcpServers"] = {}
    return raw


def _read_providers_raw(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"providers": {}}
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw.get("providers"), dict):
        raw["providers"] = {}
    return raw

FIXED_COMMANDS = [
    {"name": "plan", "description": "Toggle Plan Mode (read-only tools only)"},
    {"name": "accept-edits", "description": "Toggle Accept-Edits Mode (no approval prompts)"},
    {"name": "compact", "description": "Summarize this thread to reclaim context"},
    {"name": "clear", "description": "Wipe this thread's conversation history and start fresh"},
    {"name": "stop", "description": "Stop the current in-progress run"},
    {"name": "init", "description": "Explore the workspace and write OVERVIEW.md"},
    {"name": "startworkflow", "description": "Start recording a chain workflow"},
    {
        "name": "endworkflow",
        "description": "Stop recording and save the chain workflow (usage: /endworkflow <name>)",
    },
    {
        "name": "saveworkflow",
        "description": "Save this conversation as an agent-mode workflow "
        "(usage: /saveworkflow <name>)",
    },
    {
        "name": "runworkflow",
        "description": "Run a saved workflow now (usage: /runworkflow <name>)",
    },
    {
        "name": "saveskill",
        "description": "Save this conversation as a reusable Skill, not a replayable "
        "workflow (usage: /saveskill <name>)",
    },
]


def create_app_lg(settings: Settings | None = None) -> FastAPI:
    # Same reasoning as create_app: each provider SDK's own os.getenv() call
    # needs unprefixed keys in the real process environment, which Settings'
    # own env_file parsing doesn't provide. Same _dotenv_path() cli.py's own
    # _load_settings uses (see its docstring) -- main() below always passes
    # settings explicitly (via _load_settings), so this branch only runs for
    # a caller that constructs the app directly with settings=None.
    dotenv_path = _dotenv_path()
    # override=True: see cli.py's _load_settings docstring -- a stale
    # ambient HTTP_PROXY/HTTPS_PROXY (or any other var) already set at
    # the OS level must not silently beat what the user actually wrote
    # into .env. Redundant, harmless work for anything _load_settings()
    # (cli.py) already resolved earlier in this same process (main()
    # below always calls that first); real work for a caller that
    # constructs the app directly with settings=None, skipping that path
    # entirely. See runtime/secrets.py's resolve_env_keyring_refs.
    load_dotenv(dotenv_path, override=True)
    resolve_env_keyring_refs()
    settings = settings or Settings(_env_file=dotenv_path)  # type: ignore[call-arg]

    # Same reconciliation web/app.py's create_app already does, same
    # shared state_dir/workflow_runs storage (see this module's docstring
    # for why sharing it with the old runtime is harmless) -- a
    # WorkflowRun left at status="running" means a previous process died
    # mid-run before ever finalizing it; nothing else will ever revisit it.
    interrupted = reconcile_interrupted_runs(settings.state_dir)
    if interrupted:
        logging.getLogger(__name__).warning(
            "Marked %d workflow run(s) as failed -- still 'running' at startup, "
            "left over from a previous process that didn't shut down cleanly.",
            interrupted,
        )

    hooks_config: dict[str, list[str]] = empty_hooks_config()
    if settings.hooks_config_path is not None:
        hooks_config = load_hooks_config(settings.hooks_config_path)

    custom_providers: dict[str, dict[str, str]] = {}
    if settings.providers_config_path is not None:
        custom_providers = load_custom_providers(settings.providers_config_path)
    # Only used for its get_context_window() heuristic (see session.py) --
    # the old runtime's LLMClient.complete()/complete_stream() are never
    # called from this app, resolve_chat_model's LangChain models are. Kept
    # live-updated via register_custom_provider/deregister_custom_provider
    # in add_provider/remove_provider below (same calls web/app.py's client
    # already gets), even though the *chat* model for a new session is
    # resolved fresh from disk instead -- see _get_session below.
    context_window_client = LLMClient(custom_providers=custom_providers)

    checkpoint_path = settings.state_dir / "runtime_lg_checkpoints.sqlite"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    sessions: dict[str, ChatSessionLG] = {}
    # Set once the lifespan context is entered -- plain mutable holders
    # rather than module/globals, since create_app_lg() may be called more
    # than once (e.g. once per test). checkpointer_holder is always
    # populated before the app starts serving requests (see lifespan
    # below) -- _get_session is only reachable from ws_endpoint, which
    # can't run until the lifespan's own yield has happened.
    #
    # extra_tools_holder does NOT have that same guarantee, deliberately:
    # see lifespan's own MCP-connect comment for why a session opened
    # very early may briefly see fewer tools than a slow-to-connect MCP
    # server will eventually provide.
    checkpointer_holder: dict[str, Any] = {}
    extra_tools_holder: dict[str, list[Any]] = {"tools": []}
    # Fan-out for background-completion events (see background_events.py's
    # module docstring) -- the desktop shell's SSE client is the only
    # subscriber today, but this is a plain broadcaster, not a single-slot
    # holder, so nothing stops a future second subscriber.
    background_events = BackgroundEventBus()
    # {server_name: McpServerConnection} for every currently-connected MCP
    # server -- each owns a persistent session/subprocess (see runtime_lg/
    # mcp.py's module docstring for why persistent, not the previous
    # per-tool-call default) that must be explicitly close()d when this
    # server disconnects/reconnects or the app shuts down, or its
    # subprocess (and, for Playwright, its browser) leaks past that point.
    mcp_connections: dict[str, McpServerConnection] = {}

    def _title_sidecar_path(thread_id: str) -> Path:
        # Per-thread sidecar-file convention (plain text keyed by
        # thread_id under settings.state_dir) shared with
        # _workspace_sidecar_path/_skills_sidecar_path below -- holds a
        # user-chosen rename, overriding list_threads' default
        # "preview = first human message" title. Unlike the workspace
        # sidecar, this one is never read to configure a live session --
        # it only affects how a thread is displayed in the nav rail's
        # session list.
        return settings.state_dir / f"{thread_id}.title"

    def _workspace_sidecar_path(thread_id: str) -> Path:
        return settings.state_dir / f"{thread_id}.workspace"

    def _write_workspace_sidecar(thread_id: str, path: str) -> None:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        _workspace_sidecar_path(thread_id).write_text(path, encoding="utf-8")

    def _resolve_workspace(thread_id: str, workspace_param: str | None) -> tuple[Path, bool]:
        """"First choice wins, then sticks" -- the ?workspace= query
        param only matters the *first* time a thread_id is seen; a
        reconnect with no query param falls back to the sidecar file
        written that first time. Always has a fallback value
        (settings.workspace_root) though, so an unset thread is never
        left with "no workspace" -- every thread that never explicitly
        picked one behaves exactly like today (single global
        workspace_root).

        Returns (resolved_path, explicit) -- explicit is True iff a real
        per-thread choice exists (sidecar written this call or already on
        disk), False for the settings.workspace_root fallback. Callers
        need this alongside the path itself: comparing the resolved path
        against settings.workspace_root by value would be wrong (a user
        can deliberately choose the same directory as the default), so
        ChatSessionLG.select_workspace's "already set" guard needs this
        explicit flag, not a path comparison."""
        sidecar = _workspace_sidecar_path(thread_id)
        if workspace_param is None and sidecar.is_file():
            workspace_param = sidecar.read_text(encoding="utf-8").strip()
        elif workspace_param is not None and not sidecar.is_file():
            _write_workspace_sidecar(thread_id, workspace_param)
        if workspace_param:
            return Path(workspace_param), True
        return settings.workspace_root, False

    def _skills_by_name() -> dict[str, SkillInfo]:
        # Re-scanned on every call, not a closure snapshot -- the same
        # staleness bug switch_model's custom-providers snapshot had (see
        # runtime_lg/README.md), just for skills: settings.skills_dir can
        # gain a new entry mid-process now (POST /api/skills/upload
        # below), and both GET /api/skills and the select_skills WS
        # validation need to see it without a server restart. Skill
        # directories are few and cheap to stat, so re-scanning per call
        # (rather than invalidating a cache on upload) is the simplest
        # correct thing.
        return {s.name: s for s in load_builtin_skills() + load_skills(settings.skills_dir)}

    # load_skills(settings.skills_dir) above creates the directory as a
    # side effect (see skills.py's _scan_skills_dir) -- that used to
    # happen for free the moment the old eager `skills_by_name = {...}`
    # snapshot was built at startup. Now that the scan is lazy (only on
    # an actual GET/WS call), nothing guarantees it exists yet -- e.g.
    # /api/browse-dirs listing settings.workspace_root right after
    # startup, before any skills endpoint has ever been hit, wouldn't
    # see it. One throwaway call here keeps that startup-time side
    # effect intact without bringing back the staleness bug.
    _skills_by_name()

    # The 3 shipped skills (pptx/excel/word) default to *enabled* for a
    # brand-new thread -- unlike a user-authored local skill (settings.
    # skills_dir), which stays opt-in, since there's no way to know in
    # advance whether an arbitrary local skill's guidance is something a
    # given user actually wants applied by default. The model still
    # decides whether to actually call load_skill(name) on any given
    # turn (same "load a skill when it's relevant" judgment call as
    # before) -- being in this default set only means it's *offered*,
    # not force-loaded. A user can still turn any of them off from the
    # Skills settings tab, same toggle as always.
    default_enabled_skill_names = {s.name for s in load_builtin_skills()}

    def _skills_sidecar_path(thread_id: str) -> Path:
        return settings.state_dir / f"{thread_id}.skills"

    def _write_skills_sidecar(thread_id: str, skill_names: set[str]) -> None:
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(sorted(skill_names))
        _skills_sidecar_path(thread_id).write_text(payload, encoding="utf-8")

    def _resolve_enabled_skills(thread_id: str, skills_param: str | None) -> set[str]:
        """No "first time wins" restriction, unlike _resolve_workspace --
        skills are meant to be toggled anytime, so an explicit ?skills=
        query param on (re)connect always wins over the sidecar and
        rewrites it; only a reconnect with no query param at all falls back
        to whatever was last saved. Unknown names are dropped rather than
        rejected outright. A brand-new thread with no sidecar and no
        ?skills= at all falls back to default_enabled_skill_names (the 3
        built-ins), not the empty set -- see that variable's comment
        above."""
        sidecar = _skills_sidecar_path(thread_id)
        known = _skills_by_name().keys()
        if skills_param is not None:
            names = {n for n in skills_param.split(",") if n} & known
            _write_skills_sidecar(thread_id, names)
            return names
        if sidecar.is_file():
            try:
                saved = json.loads(sidecar.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return set(default_enabled_skill_names)
            return set(saved) & known
        return set(default_enabled_skill_names)

    def _get_session(
        thread_id: str,
        skills_param: str | None = None,
        workspace_param: str | None = None,
    ) -> ChatSessionLG:
        if thread_id not in sessions:
            # Re-read providers.json fresh for every *new* session (unlike
            # context_window_client's fixed custom_providers above) -- the
            # cheapest way to get "a provider added after startup is usable
            # in the next new thread" without a second holder to keep in
            # sync, and this file only ever runs once per new thread_id, not
            # once per message.
            session_custom_providers = (
                load_custom_providers(settings.providers_config_path)
                if settings.providers_config_path is not None
                else {}
            )
            session_start_payload = {
                "event": "SessionStart",
                "agent_name": "coordinator",
                "thread_id": thread_id,
            }
            for command in hooks_config["SessionStart"]:
                run_hook(command, session_start_payload)
            resolved_workspace, workspace_explicit = _resolve_workspace(
                thread_id, workspace_param
            )
            sessions[thread_id] = ChatSessionLG(
                thread_id=thread_id,
                settings=settings,
                context_window_client=context_window_client,
                custom_providers=session_custom_providers,
                extra_tools=extra_tools_holder["tools"],
                checkpointer=checkpointer_holder["checkpointer"],
                hooks_config=hooks_config,
                enabled_skill_names=_resolve_enabled_skills(thread_id, skills_param),
                workspace_root=resolved_workspace,
                workspace_explicit=workspace_explicit,
            )
        return sessions[thread_id]

    async def _disconnect_mcp_server_lg(name: str) -> None:
        """Strips `name`'s tools from extra_tools_holder so the *next* new
        session doesn't get them; already-open ChatSessionLG instances only
        stop seeing them once their caller also calls
        _refresh_all_sessions_extra_tools() below (every current caller
        does, right after this). Async: also closes and forgets `name`'s
        McpServerConnection, if any -- unlike before the persistent-session
        fix (runtime_lg/mcp.py), there is now a real subprocess to actually
        tear down here, not just a tool list to filter."""
        extra_tools_holder["tools"] = [
            t for t in extra_tools_holder["tools"] if get_tool_metadata(t).category != f"mcp:{name}"
        ]
        connection = mcp_connections.pop(name, None)
        if connection is not None:
            await connection.close()

    async def _connect_and_register_mcp_server_lg(name: str, config: Any) -> bool:
        """Connects one MCP server and registers its tools into
        extra_tools_holder. Returns whether it actually connected --
        connect_one_mcp_server_lg's only failure signal is an empty tool
        list, same imprecision as treating a real server that happens to
        expose zero tools as "didn't connect"; accepted here since real
        MCP servers always expose at least one tool in practice."""
        # Deferred import -- see the top-of-file comment above MCP_STARTUP_TIMEOUT_SECONDS.
        from ..runtime_lg.mcp import connect_one_mcp_server_lg

        new_tools, connection = await connect_one_mcp_server_lg(name, config)
        if not new_tools or connection is None:
            return False
        extra_tools_holder["tools"].extend(new_tools)
        mcp_connections[name] = connection
        return True

    async def _refresh_all_sessions_extra_tools() -> None:
        """Real, live-reported bug: add/remove/bump_mcp_server above only
        ever updated extra_tools_holder["tools"], which _get_session only
        reads when constructing a *brand-new* ChatSessionLG (see its own
        comment) -- a connector enabled mid-conversation never showed up
        in that same, already-open thread, only in threads started
        afterwards. Called once at the end of each of the three
        connector-mutating endpoints below (not from inside
        _disconnect_mcp_server_lg/_connect_and_register_mcp_server_lg
        themselves, since add/bump call both back to back and would
        otherwise rebuild every open session's graph twice for one
        request). Best-effort per session -- ChatSessionLG.refresh_extra_
        tools already logs-and-keeps-previous-tools on its own failure, so
        one broken session's rebuild can't block the others from picking
        up the change."""
        for session in sessions.values():
            await session.refresh_extra_tools(extra_tools_holder["tools"])

    async def _get_session_async(thread_id: str) -> ChatSessionLG:
        # poll_due_wakes takes an async get_session callback (see its own
        # docstring for why -- runtime_lg can't import ChatSessionLG
        # directly, that would be circular) -- _get_session itself is
        # plain sync, this is just the async wrapper it needs.
        return _get_session(thread_id)

    async def _wake_poll_loop() -> None:
        """Runs for the life of the web process, checking for due
        sleep_until/sleep_for/wake_on/wake_on_event requests *and* due
        Scheduled Tasks every settings.wake_poll_seconds -- the
        zero-config half of both Phase 4's suspend/resume story and the
        Scheduled Tasks feature built on top of it (cli.py's --check-wakes
        flag is the other half, for a setup that doesn't keep the web
        server running). One poll checking two stores, not two separate
        background tasks -- they share the same "periodically check for
        due things" shape. One bad poll iteration (of either kind) is
        logged and skipped, not fatal to the loop -- same defensive
        posture poll_due_wakes/poll_due_scheduled_tasks themselves take
        per item."""
        while True:
            await asyncio.sleep(settings.wake_poll_seconds)
            try:
                fired_wakes = await poll_due_wakes(settings.state_dir, _get_session_async)
                for wake in fired_wakes:
                    background_events.publish(
                        BackgroundEvent(
                            kind="wake",
                            status="completed",
                            title=wake.reason,
                            thread_id=wake.thread_id,
                        )
                    )
            except Exception:
                logging.getLogger(__name__).exception("selfwake: poll_due_wakes failed")
            try:
                fired_triggers = await poll_due_scheduled_tasks(
                    settings.state_dir, _get_session_async
                )
                for trigger in fired_triggers:
                    background_events.publish(
                        BackgroundEvent(
                            kind="scheduled_task",
                            status="failed" if trigger.last_run_status == "failed" else "completed",
                            title=trigger.name,
                            thread_id=trigger.thread_id,
                        )
                    )
            except Exception:
                logging.getLogger(__name__).exception(
                    "scheduled_tasks: poll_due_scheduled_tasks failed"
                )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
            checkpointer_holder["checkpointer"] = checkpointer
            mcp_connect_task: asyncio.Task[Any] | None = None
            if settings.mcp_config_path is not None:
                # Real, user-reported bug: this used to be a plain
                # `await connect_mcp_tools_lg(...)`, so the app didn't
                # start serving *anything* -- not even the splash/setup
                # page -- until every configured MCP server finished
                # connecting. A slow-to-start one (Playwright launching a
                # real browser process is the worst case) meant a real,
                # repeatable multi-minute wait on every single cold
                # start, unrelated to which desktop shell was used.
                # Mirrors Claude Code's own real fix for the identical
                # problem (MCP_CONNECT_TIMEOUT_MS, default 5s): wait up
                # to MCP_STARTUP_TIMEOUT_SECONDS, then stop blocking
                # startup on it -- asyncio.shield() keeps the connect
                # task itself running rather than cancelling it just
                # because this wait_for gave up on it.
                # Deferred import -- see the top-of-file comment above MCP_STARTUP_TIMEOUT_SECONDS.
                from ..runtime_lg.mcp import connect_mcp_tools_lg

                connect_task: asyncio.Task[Any] = asyncio.create_task(
                    connect_mcp_tools_lg(settings.mcp_config_path)
                )
                mcp_connect_task = connect_task
                try:
                    tools, connections = await asyncio.wait_for(
                        asyncio.shield(connect_task), timeout=MCP_STARTUP_TIMEOUT_SECONDS
                    )
                    extra_tools_holder["tools"] = tools
                    mcp_connections.update(connections)
                    mcp_connect_task = None
                except TimeoutError:
                    # Still connecting -- let the app start serving
                    # requests now (a session opened in this window
                    # simply starts with fewer tools, same as any
                    # mid-conversation connector add/remove already
                    # behaves) and splice the result in once it's ready,
                    # reusing the exact mechanism a live connector-add
                    # already uses to reach already-open sessions.
                    async def _finish_mcp_connect_in_background(
                        task: asyncio.Task[Any],
                    ) -> None:
                        try:
                            tools, connections = await task
                        except Exception:
                            logging.getLogger(__name__).exception(
                                "connect_mcp_tools_lg: background connect failed"
                            )
                            return
                        extra_tools_holder["tools"] = extra_tools_holder["tools"] + tools
                        mcp_connections.update(connections)
                        await _refresh_all_sessions_extra_tools()

                    asyncio.create_task(_finish_mcp_connect_in_background(connect_task))
            wake_poll_task = asyncio.create_task(_wake_poll_loop())
            yield
            wake_poll_task.cancel()
            try:
                await wake_poll_task
            except asyncio.CancelledError:
                pass
            if mcp_connect_task is not None and not mcp_connect_task.done():
                mcp_connect_task.cancel()
                try:
                    await mcp_connect_task
                except (asyncio.CancelledError, Exception):
                    pass
            # Each connection now owns a real, persistent subprocess (see
            # runtime_lg/mcp.py's module docstring) -- close them all here
            # rather than letting them leak past this process's own
            # shutdown (a lingering Playwright browser, most visibly).
            for connection in mcp_connections.values():
                await connection.close()

    app = FastAPI(lifespan=lifespan)
    # Exposed on app.state so tests can reach the same bus _wake_poll_loop
    # publishes to without going through the (necessarily infinite, so not
    # directly awaitable-to-completion) SSE endpoint itself.
    app.state.background_events = background_events

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/internal/events")
    async def background_events_stream() -> StreamingResponse:
        """Server-Sent Events stream of background_events.BackgroundEvent
        payloads -- see that module's docstring for why this exists.
        Under `/internal/` rather than `/api/` since this isn't for the
        bundled frontend (which already gets live updates over its own
        per-thread WebSocket) -- the one real subscriber is the desktop
        shell (office-agent-desktop), connecting once at startup to show a
        native notification when a background/scheduled run finishes
        while its window is hidden. No auth beyond "reachable on
        127.0.0.1 at all," same as every other endpoint here -- this
        process already assumes a single local user.
        """

        async def event_source() -> AsyncIterator[str]:
            queue = background_events.subscribe()
            try:
                while True:
                    event = await queue.get()
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                background_events.unsubscribe(queue)

        return StreamingResponse(event_source(), media_type="text/event-stream")

    @app.get("/api/threads")
    async def list_threads() -> list[dict[str, Any]]:
        # Direct port of web/app.py's identical endpoint would glob
        # settings.state_dir for *.json files -- wrong storage entirely
        # here: runtime_lg persists conversation history in the shared
        # AsyncSqliteSaver checkpointer (runtime_lg_checkpoints.sqlite),
        # never as one JSON file per thread, so that glob always came back
        # empty and the session-switcher UI (which calls this) had no
        # history to show, even though every thread's real state was
        # right there in the checkpoint database. Queries the checkpoints
        # table directly instead -- confirmed by inspecting AsyncSqliteSaver
        # (no built-in "list every thread" method exists on the class
        # itself, only per-thread aget_tuple/alist). setup() is idempotent
        # (a no-op once already run) and creates the checkpoints/writes
        # tables if they don't exist yet -- needed here since this query
        # goes around the checkpointer's own API, which normally triggers
        # setup() lazily on first real use; a brand-new app with no
        # conversations yet would otherwise 500 on "no such table".
        checkpointer = checkpointer_holder["checkpointer"]
        await checkpointer.setup()
        cursor = await checkpointer.conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
        )
        rows = await cursor.fetchall()
        # Real per-thread metadata (Phase 2 of ROADMAP.md), not just bare
        # ids -- aget_tuple(thread_id) fetches each thread's *latest*
        # checkpoint directly off the checkpointer, without needing a
        # compiled Agent graph the way send_history's aget_state does
        # (that needs self.lg_agent; this needs nothing but a thread_id).
        # checkpoint["channel_values"]["messages"] is the exact same
        # channel send_history reads via state.values -- confirmed live,
        # not just by type signature, against a real checkpoint written
        # through AsyncSqliteSaver.aput.
        summaries: list[dict[str, Any]] = []
        for (thread_id,) in rows:
            tuple_ = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
            messages = (
                list(tuple_.checkpoint["channel_values"].get("messages", [])) if tuple_ else []
            )
            preview = ""
            for message in messages:
                if getattr(message, "type", None) == "human":
                    preview = strip_mode_note(extract_text(message.content))
                    break
            workspace_sidecar = _workspace_sidecar_path(thread_id)
            workspace_root = (
                workspace_sidecar.read_text(encoding="utf-8").strip()
                if workspace_sidecar.is_file()
                else str(settings.workspace_root)
            )
            title_sidecar = _title_sidecar_path(thread_id)
            if title_sidecar.is_file():
                preview = title_sidecar.read_text(encoding="utf-8").strip()
            summaries.append(
                {
                    "thread_id": thread_id,
                    "updated_at": tuple_.checkpoint["ts"] if tuple_ else None,
                    "message_count": len(messages),
                    "preview": preview[:200],
                    "workspace_root": workspace_root,
                }
            )
        summaries.sort(key=lambda s: s["updated_at"] or "", reverse=True)
        return summaries

    @app.delete("/api/threads/{thread_id}")
    async def delete_thread(thread_id: str) -> JSONResponse:
        # Direct-storage counterpart to list_threads above -- web/app.py's
        # identical endpoint deletes a FileStateStore-persisted RunState;
        # this deletes the thread's real checkpoints instead. .tasks.json
        # and the sidecar files aren't part of the checkpointer's own
        # domain but belong to the same thread, so a delete needs to clean
        # them up too -- otherwise a new thread later reusing the same id
        # would inherit an old task list or workspace/skills choice from a
        # "deleted" conversation (same reasoning as web/app.py's identical
        # cleanup).
        checkpointer = checkpointer_holder["checkpointer"]
        await checkpointer.setup()  # see list_threads' identical comment
        cursor = await checkpointer.conn.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1", (thread_id,)
        )
        existed = await cursor.fetchone() is not None
        await checkpointer.adelete_thread(thread_id)
        (settings.state_dir / f"{thread_id}.tasks.json").unlink(missing_ok=True)
        _skills_sidecar_path(thread_id).unlink(missing_ok=True)
        _workspace_sidecar_path(thread_id).unlink(missing_ok=True)
        _title_sidecar_path(thread_id).unlink(missing_ok=True)
        sessions.pop(thread_id, None)
        if not existed:
            return JSONResponse({"error": f"No thread {thread_id!r}"}, status_code=404)
        return JSONResponse({"deleted": thread_id})

    @app.post("/api/threads/{thread_id}/rename")
    async def rename_thread(thread_id: str, payload: ThreadRename) -> JSONResponse:
        title = payload.title.strip()
        if not title:
            return JSONResponse({"error": "title cannot be blank"}, status_code=400)
        checkpointer = checkpointer_holder["checkpointer"]
        await checkpointer.setup()  # see list_threads' identical comment
        cursor = await checkpointer.conn.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1", (thread_id,)
        )
        if await cursor.fetchone() is None:
            return JSONResponse({"error": f"No thread {thread_id!r}"}, status_code=404)
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        _title_sidecar_path(thread_id).write_text(title[:200], encoding="utf-8")
        return JSONResponse({"thread_id": thread_id, "title": title[:200]})

    @app.get("/api/threads/{thread_id}/tasks")
    async def get_tasks(thread_id: str) -> list[dict[str, Any]]:
        return TaskToolkit(thread_id, settings.state_dir).list_tasks()

    # -- /api/workflows, /api/workflow-runs -- direct ports of web/app.py's
    # identical endpoints (see this module's docstring for the general
    # "next new session only" caveat, which doesn't apply here: these are
    # pure WorkflowStore/WorkflowRunStore reads/writes, nothing session- or
    # graph-specific about them).

    @app.get("/api/workflows")
    async def list_workflows_endpoint() -> list[dict[str, Any]]:
        return [w.to_dict() for w in WorkflowStore(settings.state_dir).list_all()]

    @app.delete("/api/workflows/{name}")
    async def delete_workflow_endpoint(name: str) -> JSONResponse:
        if not WorkflowStore(settings.state_dir).delete(name):
            return JSONResponse({"error": f"No workflow named {name!r}"}, status_code=404)
        return JSONResponse({"deleted": name})

    @app.get("/api/workflow-runs")
    async def list_workflow_runs(limit: int = 20) -> list[dict[str, Any]]:
        return [r.to_dict() for r in WorkflowRunStore(settings.state_dir).list_recent(limit)]

    @app.get("/api/workflow-runs/{run_id}")
    async def get_workflow_run(run_id: str) -> JSONResponse:
        run = WorkflowRunStore(settings.state_dir).load(run_id)
        if run is None:
            return JSONResponse({"error": f"No run {run_id!r}"}, status_code=404)
        return JSONResponse(run.to_dict())

    @app.delete("/api/workflow-runs/{run_id}")
    async def delete_workflow_run_endpoint(run_id: str) -> JSONResponse:
        if not WorkflowRunStore(settings.state_dir).delete(run_id):
            return JSONResponse({"error": f"No run {run_id!r}"}, status_code=404)
        return JSONResponse({"deleted": run_id})

    # -- /api/scheduled-tasks -- the Settings > Scheduled Tasks panel's
    # create-without-a-conversation entry point; direct ScheduledTriggerStore
    # reads/writes, same "no session/graph involved" shape as the
    # /api/workflows endpoints just above. POST reuses create_trigger
    # (tools/scheduled_tasks.py) -- the exact same validation
    # create_scheduled_task (the model tool) uses, so the two creation
    # paths can't silently drift apart.

    @app.get("/api/scheduled-tasks")
    async def list_scheduled_tasks_endpoint() -> list[dict[str, Any]]:
        return [t.to_dict() for t in ScheduledTriggerStore(settings.state_dir).list_all()]

    @app.post("/api/scheduled-tasks")
    async def create_scheduled_task_endpoint(payload: ScheduledTaskCreate) -> JSONResponse:
        try:
            trigger = create_trigger(
                ScheduledTriggerStore(settings.state_dir),
                WorkflowStore(settings.state_dir),
                name=payload.name,
                kind=payload.kind,
                at=payload.at,
                prompt=payload.prompt,
                workflow_name=payload.workflow_name,
                weekday=payload.weekday,
                day_of_month=payload.day_of_month,
            )
        except (ValueError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(trigger.to_dict())

    @app.post("/api/scheduled-tasks/{trigger_id}/pause")
    async def pause_scheduled_task_endpoint(trigger_id: str) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        if trigger is None:
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        trigger.enabled = False
        store.save(trigger)
        return JSONResponse(trigger.to_dict())

    @app.post("/api/scheduled-tasks/{trigger_id}/resume")
    async def resume_scheduled_task_endpoint(trigger_id: str) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        if trigger is None:
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        trigger.enabled = True
        trigger.next_run_at = compute_next_run_at(trigger.schedule, datetime.now())
        store.save(trigger)
        return JSONResponse(trigger.to_dict())

    @app.delete("/api/scheduled-tasks/{trigger_id}")
    async def delete_scheduled_task_endpoint(trigger_id: str) -> JSONResponse:
        if not ScheduledTriggerStore(settings.state_dir).delete(trigger_id):
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        return JSONResponse({"deleted": trigger_id})

    @app.get("/api/commands")
    async def get_commands() -> list[dict[str, str]]:
        commands = list(FIXED_COMMANDS)
        for skill in load_builtin_skills() + load_skills(settings.skills_dir):
            # skill.slug, not skill.name -- "name" here means "the literal
            # token typed after /", same as every FIXED_COMMANDS entry
            # above (e.g. "accept-edits", not a display label); a skill's
            # own display name can contain spaces ("Skill Creator") and
            # was never usable as that token to begin with -- see
            # web/session.py's skills_by_slug for the matching half.
            commands.append({"name": skill.slug, "description": skill.description})
        return commands

    @app.get("/api/tools")
    async def get_tools() -> dict[str, Any]:
        agent = build_coordinator_agent(settings, thread_id="__tools_probe__")
        tools = []
        for tool in agent.tools:
            metadata = get_tool_metadata(tool)
            doc = inspect.getdoc(tool) or ""
            description = doc.splitlines()[0] if doc else ""
            tools.append(
                {
                    "name": tool.__name__,
                    "category": metadata.category or "",
                    "risk_category": metadata.risk_category,
                    "requires_approval": metadata.requires_approval,
                    "description": description,
                }
            )
        return {"tools": tools}

    @app.get("/api/skills")
    async def get_skills() -> list[dict[str, str]]:
        # Built-in + user-local, same combined set build_coordinator_agent
        # itself splices when skill_names=None -- the Skills settings tab
        # renders one checkbox per entry here. `source` lets the frontend
        # split the list into "Your skills" (custom) / "Discover"
        # (built-in) tabs -- default_enabled_skill_names is exactly the
        # built-in name set already computed above, reused rather than
        # scanning builtin_skills/ a second time.
        return [
            {
                "name": s.name,
                "description": s.description,
                "source": "builtin" if s.name in default_enabled_skill_names else "custom",
            }
            for s in _skills_by_name().values()
        ]

    @app.post("/api/skills/upload")
    async def upload_skill(file: UploadFile) -> JSONResponse:
        # The real half of Settings > Skills > Add > Upload skill (see
        # tools/skills.py's save_uploaded_skill for the accepted shapes
        # and docs/ui-references/skills-add-uploadskills.png for the
        # reference UI). Always writes into settings.skills_dir, i.e.
        # always a "custom" skill -- there's no UI path to add a builtin
        # one, those only ever come from the package itself.
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "file too large (max 25MB)"}, status_code=413)
        try:
            skill = save_uploaded_skill(settings.skills_dir, file.filename or "", content)
        except SkillUploadError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(
            {"name": skill.name, "description": skill.description, "source": "custom"}
        )

    @app.post("/api/upload")
    async def upload_file(file: UploadFile) -> JSONResponse:
        # Direct port of web/app.py's identical endpoint -- pure file I/O
        # against settings.workspace_root, nothing runtime-specific about
        # it (unlike /api/config, /api/mcp/*, /api/providers/*, this one
        # has no "next new session only" wrinkle: an uploaded file just
        # needs to exist on disk before the model's next tool call reads
        # it, which every already-open ChatSessionLG can do immediately).
        content = await file.read()
        if len(content) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "file too large (max 25MB)"}, status_code=413)
        scope = WorkspaceScope(settings.workspace_root)
        path = _unique_upload_path(scope, file.filename or "upload")
        path.write_bytes(content)
        return JSONResponse({"path": scope.relative(path), "bytes_written": len(content)})

    @app.get("/api/previews/{name}")
    async def get_preview(name: str) -> Response:
        # Serves the write_docx/write_xlsx/write_pptx thumbnails written by
        # tools/_thumbnail.py's render_thumbnail under
        # settings.state_dir/previews/ -- not a general file-access endpoint
        # (unlike a WorkspaceScope-backed route, there's no user-supplied
        # path to sanitize against traversal here: `name` is checked against
        # the exact `<32 hex chars>.png` shape render_thumbnail always
        # generates, so it can only ever resolve to a plain filename inside
        # that one directory).
        if not _PREVIEW_NAME_RE.fullmatch(name):
            return JSONResponse({"error": "not found"}, status_code=404)
        preview_path = settings.state_dir / "previews" / name
        if not preview_path.is_file():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(preview_path, media_type="image/png")

    @app.get("/api/pptx-shapes")
    async def get_pptx_shapes(path: str, slide: int) -> JSONResponse:
        # Backs the click-a-shape-in-the-preview-to-target-it feature
        # (ChatLog.tsx's PptxShapeOverlay): the frontend already has
        # `path` from the tool call's own `arguments.path` and picks
        # `slide` from `arguments.slide` (edits) or defaults to 1 (a
        # fresh write_pptx), then overlays clickable regions on top of
        # the already-rendered preview image using this endpoint's
        # inch-based bboxes (converted to on-screen percentages -- see
        # PresentationToolkit.list_pptx_shapes's own docstring for why
        # inches, not pixels). Same underlying method the LLM-facing
        # list_pptx_shapes tool calls -- this is a plain, ungated REST
        # read, not a tool call, since it's UI-only (never reaches the
        # model, never touches the audit log a real tool call would).
        from ..tools.presentations import PresentationToolkit

        toolkit = PresentationToolkit(settings.workspace_root, state_dir=settings.state_dir)
        try:
            result = await asyncio.to_thread(toolkit.list_pptx_shapes, path=path, slide=slide)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(result)

    # -- /api/config, /api/mcp/*, /api/providers/* -- direct ports of
    # web/app.py's identical endpoints (see this module's docstring for the
    # one behavioral difference: config changes here apply to the next new
    # session, not every already-open one).

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        values = dotenv_values(".env")
        result: dict[str, Any] = {}
        for key in PROVIDER_KEY_ENV_VARS:
            value = env_resolve_secret_for_display(values.get(key) or None)
            result[key] = {"set": bool(value), "masked": _mask(value) if value else None}
        for key in PROVIDER_DEFAULT_MODEL_ENV_VARS:
            result[key] = values.get(key) or None
        for key in COSCRIBE_ENV_VARS:
            result[key] = values.get(key) or None
        for key in DESKTOP_ENV_VARS:
            result[key] = values.get(key) or None
        return result

    @app.post("/api/config")
    async def update_config(payload: ConfigUpdate) -> dict[str, Any]:
        allowed = (
            set(PROVIDER_KEY_ENV_VARS)
            | set(PROVIDER_DEFAULT_MODEL_ENV_VARS)
            | set(COSCRIBE_ENV_VARS)
            | set(DESKTOP_ENV_VARS)
        )
        rejected: dict[str, str] = {}
        applied: set[str] = set()
        for key, value in payload.updates.items():
            if key not in allowed:
                continue
            if key in BLANK_UNSAFE_ENV_VARS and not value.strip():
                rejected[key] = "cannot be blank"
                continue
            if key == "COSCRIBE_DEFAULT_MODEL" and ":" not in value:
                rejected[key] = 'must be a "provider:model" string, e.g. "anthropic:sonnet"'
                continue
            if key == "COSCRIBE_MAX_TURNS" and not (value.strip().isdigit() and int(value) > 0):
                rejected[key] = "must be a positive integer"
                continue
            if key in PROVIDER_DEFAULT_MODEL_ENV_VARS and ":" in value:
                rejected[key] = (
                    'must be a bare model id, e.g. "claude-opus-5" -- no "provider:" prefix'
                )
                continue
            if key in PROVIDER_KEY_ENV_VARS:
                set_key(".env", key, env_value_for_storage(f"builtin-provider:{key}", value))
                harden_file_permissions(Path(".env"))
                # Mirror the real value into the process environment too,
                # same reason web/app.py's update_config does -- each
                # provider SDK's own constructor reads straight from
                # os.environ, and a plain .env-file write (possibly now a
                # keyring-ref sentinel) is invisible to this already-running
                # process until a restart. Unlike web/app.py, there's no
                # client.invalidate_provider(...) call needed here:
                # resolve_chat_model (runtime_lg/providers.py) builds a
                # fresh ChatAnthropic/ChatGoogleGenerativeAI/ChatOpenAI
                # instance from scratch on every call, no cached instance to
                # go stale in the first place.
                os.environ[key] = value
            else:
                set_key(".env", key, value)
            applied.add(key)
        restart_required = any(key in COSCRIBE_ENV_VARS for key in applied)
        return {"restart_required": restart_required, "rejected": rejected}

    @app.get("/api/memory")
    async def get_memory() -> dict[str, Any]:
        # Settings.memory_path can change mid-session (a workspace switch
        # re-resolves it, see select_workspace) -- reading settings.memory_path
        # fresh here rather than caching it at app-build time keeps this
        # endpoint honest about whichever file the *next* new thread would
        # actually load, same "read fresh, no stale cache" posture
        # get_config's own dotenv_values(".env") call takes.
        return {"content": load_memory(settings.memory_path)}

    @app.post("/api/memory")
    async def update_memory(payload: MemoryUpdate) -> dict[str, Any]:
        # Directly overwrites the file -- the Settings panel's own text
        # box is the whole editing surface here (unlike the `remember`
        # tool, which only ever appends one bullet at a time), so a full
        # overwrite is the correct semantics for "save what's in the box."
        # Same "next new thread only" gap this project already accepts
        # for provider/skill config changes (see this module's own
        # docstring) -- an already-open thread keeps whatever memory
        # content it started with until its process restarts or a fresh
        # thread opens.
        path = Path(settings.memory_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload.content, encoding="utf-8")
        return {"status": "ok"}

    @app.get("/api/browse-dirs")
    async def browse_dirs(path: str | None = None) -> dict[str, Any]:
        # Direct port of the old hand-rolled runtime's identical endpoint
        # (deleted in the web cutover, see runtime_lg/README.md) -- missing
        # here entirely was a real live-reported bug: app.js (shared by
        # both backends while they coexisted) drives this folder-browser
        # modal for the Settings panel's extra_readable_dirs/
        # extra_writable_dirs picker, but this app didn't define the route
        # yet at the time, so the fetch 404'd and the modal opened empty/
        # broken with no error surfaced. Same deliberately-unrestricted
        # trust model as before: no auth, local-only, and the user could
        # already type any absolute path into that field by hand.
        target = Path(path).expanduser() if path else Path.home()
        try:
            resolved = target.resolve()
        except OSError as exc:
            return {"error": str(exc)}
        if not resolved.is_dir():
            return {"error": f"Not a directory: {resolved}"}
        directories = []
        try:
            for entry in resolved.iterdir():
                try:
                    if entry.is_dir():
                        directories.append({"name": entry.name, "path": str(entry)})
                except OSError:
                    continue  # unreadable entry (permissions, broken link, ...) -- skip it
        except OSError as exc:
            return {"error": str(exc)}
        return {
            "path": str(resolved),
            "parent": str(resolved.parent) if resolved.parent != resolved else None,
            "directories": sorted(directories, key=lambda d: d["name"].lower()),
        }

    @app.get("/api/mcp/catalog")
    async def get_mcp_catalog() -> list[dict[str, Any]]:
        return MCP_CATALOG

    @app.get("/api/mcp/browser-check")
    async def check_browser() -> dict[str, Any]:
        if sys.platform != "win32":
            return {"checked": False, "path": None}
        return {"checked": True, "path": find_windows_browser()}

    @app.post("/api/mcp/install-browser")
    async def install_browser() -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                # Resolved via shutil.which -- same Windows PATHEXT bug
                # runtime_lg/mcp.py's _to_lg_connection already documents.
                [shutil.which("npx") or "npx", "playwright", "install", "chromium"],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"success": False, "error": str(exc)}
        if result.returncode != 0:
            return {"success": False, "error": result.stderr[-2000:] or "install failed"}
        return {"success": True}

    @app.get("/api/mcp/servers")
    async def get_mcp_servers() -> dict[str, Any]:
        if settings.mcp_config_path is None or not settings.mcp_config_path.is_file():
            return {}
        result: dict[str, Any] = {}
        for name, config in load_mcp_server_configs(settings.mcp_config_path).items():
            # `connected` reads the same live mcp_connections registry
            # every actual tool call goes through (see its own comment
            # above) -- a real signal, not derived from the static config
            # this loop is otherwise reading. Configured but not currently
            # connected covers both "never successfully connected" and
            # "connected once, then the subprocess/session died" -- this
            # endpoint doesn't distinguish those, same as the Connectors
            # tab never has (add/bump already surface a real error message
            # at the point of failure; this is just current live state).
            connected = name in mcp_connections
            if "server_url" in config:
                # Remote (streamable_http) entry -- a hand-configured
                # Custom-tab remote-server form. Bearer/auth header values
                # masked the same way env values are below -- never echoed
                # back to the browser in full.
                result[name] = {
                    "server_url": config["server_url"],
                    "masked_headers": {
                        k: _mask(v) for k, v in (config.get("headers") or {}).items()
                    },
                    "connected": connected,
                }
            else:
                result[name] = {
                    "command": config.get("command"),
                    "args": config.get("args", []),
                    "masked_env": {k: _mask(v) for k, v in (config.get("env") or {}).items()},
                    "connected": connected,
                }
        return result

    @app.post("/api/mcp/servers")
    async def add_mcp_server(payload: MCPServerUpdate) -> dict[str, Any]:
        if payload.server_url:
            entry: dict[str, Any] = {"server_url": payload.server_url}
            if payload.headers:
                entry["headers"] = payload.headers
        else:
            entry = {"command": payload.command, "args": payload.args}
            if payload.env:
                entry["env"] = payload.env
        try:
            config = validate_mcp_config({"type": "mcp", "name": payload.name, **entry})
        except ValueError as exc:
            return {"rejected": {payload.name: str(exc)}, "connected": False}

        # .resolve() -- see add_provider's identical fallback for why a
        # bare relative path here is a real, cwd-dependent bug.
        path = settings.mcp_config_path or Path("./mcp.json").resolve()
        raw = _read_mcp_servers_raw(path)
        # `config` above (used to actually connect, just below) keeps the
        # real env/headers values; only what's persisted to disk gets
        # routed through store_secret per value.
        stored_entry = dict(entry)
        if payload.env:
            stored_entry["env"] = {
                key: store_secret(f"mcp:{payload.name}:env:{key}", value)
                for key, value in payload.env.items()
            }
        if payload.headers:
            stored_entry["headers"] = {
                key: store_secret(f"mcp:{payload.name}:headers:{key}", value)
                for key, value in payload.headers.items()
            }
        raw["mcpServers"][payload.name] = stored_entry
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        harden_file_permissions(path)
        if settings.mcp_config_path is None:
            settings.mcp_config_path = path
            set_key(".env", "COSCRIBE_MCP_CONFIG_PATH", str(path))

        await _disconnect_mcp_server_lg(payload.name)
        connected = await _connect_and_register_mcp_server_lg(payload.name, config)
        await _refresh_all_sessions_extra_tools()
        return {"rejected": {}, "connected": connected}

    @app.delete("/api/mcp/servers/{name}")
    async def remove_mcp_server(name: str) -> dict[str, Any]:
        await _disconnect_mcp_server_lg(name)
        if settings.mcp_config_path is not None and settings.mcp_config_path.is_file():
            raw = _read_mcp_servers_raw(settings.mcp_config_path)
            removed = raw["mcpServers"].pop(name, None)
            if removed is not None:
                for value in (removed.get("env") or {}).values():
                    delete_secret(value)
                for value in (removed.get("headers") or {}).values():
                    delete_secret(value)
            settings.mcp_config_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
            harden_file_permissions(settings.mcp_config_path)
        await _refresh_all_sessions_extra_tools()
        return {}

    @app.get("/api/mcp/npm-latest-version")
    async def npm_latest_version(package: str) -> dict[str, Any]:
        if not _NPM_PACKAGE_NAME_RE.match(package):
            return {"error": "not a valid npm package name"}
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                [shutil.which("npm") or "npm", "view", package, "version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"error": str(exc)}
        if result.returncode != 0:
            return {"error": (result.stderr.strip() or "npm view failed")[-500:]}
        return {"package": package, "latest": result.stdout.strip()}

    @app.post("/api/mcp/servers/{name}/bump-version")
    async def bump_mcp_server_version(name: str, payload: MCPVersionBump) -> dict[str, Any]:
        if settings.mcp_config_path is None or not settings.mcp_config_path.is_file():
            return {"error": "not found", "connected": False}
        raw = _read_mcp_servers_raw(settings.mcp_config_path)
        entry = raw["mcpServers"].get(name)
        if entry is None:
            return {"error": "not found", "connected": False}
        prefix = f"{payload.package}@"
        entry["args"] = [
            f"{payload.package}@{payload.version}" if arg.startswith(prefix) else arg
            for arg in entry.get("args", [])
        ]
        try:
            config = validate_mcp_config({"type": "mcp", "name": name, **entry})
        except ValueError as exc:
            return {"error": str(exc), "connected": False}
        raw["mcpServers"][name] = entry
        settings.mcp_config_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        await _disconnect_mcp_server_lg(name)
        connected = await _connect_and_register_mcp_server_lg(name, config)
        await _refresh_all_sessions_extra_tools()
        return {"connected": connected}

    @app.get("/api/providers/catalog")
    async def get_providers_catalog() -> list[dict[str, Any]]:
        builtin_entries: list[dict[str, Any]] = [
            {
                "name": "anthropic",
                "description": "Anthropic's Claude models.",
                "base_url": "",
                # Unlike the third-party PROVIDER_CATALOG below, this one
                # is worth pinning to a real model ID -- Anthropic doesn't
                # publish a rolling "-latest" alias the way gemini's own
                # "gemini-flash-latest" entry below does, so an empty
                # default would leave the single most common Add-provider
                # path with the worst experience of any entry here.
                # Still real drift, caught live: this was "claude-opus-4-6"
                # until an unrelated bug report exposed it as already
                # stale (no such model -- the current family is Opus 5/
                # Sonnet 5/Haiku 4.5). Whoever bumps coscribe's own
                # supported-model docs should bump this alongside them.
                "default_model": "claude-opus-5",
                "builtin": True,
            },
            {
                "name": "openai",
                "description": "OpenAI's GPT models.",
                "base_url": "",
                "default_model": "",
                "builtin": True,
            },
            {
                "name": "gemini",
                "description": "Google's Gemini models.",
                "base_url": "",
                "default_model": "gemini-flash-latest",
                "builtin": True,
            },
        ]
        custom_entries = [{**entry, "builtin": False} for entry in PROVIDER_CATALOG]
        return [*builtin_entries, *custom_entries]

    @app.get("/api/providers")
    async def get_providers() -> dict[str, Any]:
        env_values = dotenv_values(".env")
        result: dict[str, Any] = {}
        for provider in BUILTIN_PROVIDERS:
            api_key = env_resolve_secret_for_display(env_values.get(provider["api_key_env"])) or ""
            if not api_key:
                continue
            result[provider["key"]] = {
                "base_url": None,
                "default_model": env_values.get(provider["default_model_env"]) or "",
                "masked_key": _mask(api_key),
                "builtin": True,
            }
        if settings.providers_config_path is not None and settings.providers_config_path.is_file():
            raw = _read_providers_raw(settings.providers_config_path)
            for name, entry in raw["providers"].items():
                api_key = resolve_secret(entry.get("api_key")) or ""
                result[name] = {
                    "base_url": entry.get("base_url", ""),
                    "default_model": entry.get("default_model", ""),
                    "masked_key": _mask(api_key) if api_key else None,
                    "builtin": False,
                }
        return result

    @app.post("/api/providers")
    async def add_provider(payload: ProviderUpdate) -> dict[str, Any]:
        name = payload.name.strip()
        if not name:
            return {"restart_required": False, "rejected": {"name": "cannot be blank"}}
        if not payload.api_key.strip():
            return {"restart_required": False, "rejected": {name: "api_key cannot be blank"}}

        builtin = next((p for p in BUILTIN_PROVIDERS if p["key"] == name.lower()), None)
        if builtin is not None:
            env_key = builtin["api_key_env"]
            set_key(
                ".env",
                env_key,
                env_value_for_storage(f"builtin-provider:{env_key}", payload.api_key),
            )
            harden_file_permissions(Path(".env"))
            # os.environ gets the real value, not whatever .env just got
            # (a keyring ref sentinel there) -- this live process needs the
            # actual key now, not after the next resolve_env_keyring_refs()
            # startup pass.
            os.environ[builtin["api_key_env"]] = payload.api_key
            if payload.default_model:
                set_key(".env", builtin["default_model_env"], payload.default_model)
            # No context_window_client.invalidate_provider(...) equivalent
            # needed for the *chat* model -- resolve_chat_model builds fresh
            # every call (see update_config's identical comment above).
            # context_window_client itself is the old-runtime LLMClient
            # class, though, so it keeps that class's real caching behavior
            # and does need this.
            context_window_client.invalidate_provider(builtin["key"])
            return {"restart_required": False, "rejected": {}}

        if not payload.base_url.strip():
            return {"restart_required": False, "rejected": {name: "base_url cannot be blank"}}

        # Stored on disk via store_secret (keyring ref when available, the
        # real value as a hardened-permission fallback otherwise) --
        # context_window_client.register_custom_provider below keeps using
        # payload.api_key directly (the real value), never this.
        entry: dict[str, Any] = {
            "base_url": payload.base_url,
            "api_key": store_secret(f"custom-provider:{name}", payload.api_key),
        }
        if payload.default_model:
            entry["default_model"] = payload.default_model

        # .resolve() matters here: this path gets persisted both into
        # settings.providers_config_path (kept for the rest of the process's
        # life) and into .env (read back on every future process start) --
        # a bare relative "providers.json" would silently start pointing at
        # a different file the moment the process's cwd ever changes.
        # Confirmed the hard way: a stray real .env in the repo root with a
        # relative COSCRIBE_PROVIDERS_CONFIG_PATH from a previous manual
        # run made a batch of unrelated tests fail with FileNotFoundError,
        # purely because they didn't all chdir the same way.
        path = settings.providers_config_path or Path("./providers.json").resolve()
        raw = _read_providers_raw(path)
        raw["providers"][name] = entry
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        harden_file_permissions(path)
        if settings.providers_config_path is None:
            settings.providers_config_path = path
            set_key(".env", "COSCRIBE_PROVIDERS_CONFIG_PATH", str(path))
        # Only context_window_client needs live registration -- the next
        # new session's own model resolves this provider by re-reading
        # providers.json fresh (see _get_session above), not through this
        # client at all.
        context_window_client.register_custom_provider(
            name, {"base_url": payload.base_url, "api_key": payload.api_key}
        )
        return {"restart_required": False, "rejected": {}}

    @app.delete("/api/providers/{name}")
    async def remove_provider(name: str) -> dict[str, Any]:
        builtin = next((p for p in BUILTIN_PROVIDERS if p["key"] == name.lower()), None)
        if builtin is not None:
            env_delete_secret_if_ref(dotenv_values(".env").get(builtin["api_key_env"]))
            set_key(".env", builtin["api_key_env"], "")
            os.environ.pop(builtin["api_key_env"], None)
            context_window_client.invalidate_provider(builtin["key"])
            return {"restart_required": False}
        if settings.providers_config_path is None or not settings.providers_config_path.is_file():
            return {"restart_required": False}
        raw = _read_providers_raw(settings.providers_config_path)
        removed = raw["providers"].pop(name, None)
        if removed is not None:
            delete_secret(removed.get("api_key"))
        settings.providers_config_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
        harden_file_permissions(settings.providers_config_path)
        context_window_client.deregister_custom_provider(name)
        return {"restart_required": False}

    # Every handler below wraps its real work in asyncio.to_thread --
    # list_packages/install_package/uninstall_package/set_interpreter_override
    # all shell out via subprocess.run with multi-minute timeouts (venv
    # creation alone allows 120s, baseline package seeding 300s -- see
    # tools/script_env.py's _VENV_TIMEOUT/_SETUP_TIMEOUT). Calling them
    # directly from an `async def` route handler, as this code did before,
    # runs that blocking subprocess wait *on the single asyncio event
    # loop* -- not just stalling this one HTTP response, but freezing
    # every other request this whole process serves for as long as pip
    # takes: other REST calls (Settings' other tabs all "went empty"),
    # the WebSocket chat loop (a sent message got no response at all,
    # looking exactly like a dropped connection), everything. Real,
    # live-reported bug: setting a new interpreter override (which
    # deletes the existing script-env venv, see set_interpreter_override's
    # docstring) followed by an Add-package click rebuilt the venv from
    # scratch and reseeded 5 baseline packages over the network -- a
    # multi-minute stretch during which the whole app looked dead. This
    # bug already existed before the interpreter picker (any first-ever
    # venv creation hit it too), just rarely enough to go unnoticed; the
    # picker's rebuild-on-change behavior made it easy to trigger on
    # purpose and land squarely in the recovery flow meant to fix a
    # broken setup.
    @app.get("/api/script-env/packages")
    async def get_script_env_packages() -> list[dict[str, str]]:
        return await asyncio.to_thread(list_packages, settings.state_dir)

    @app.post("/api/script-env/packages")
    async def add_script_env_package(payload: ScriptEnvPackageInstall) -> dict[str, object]:
        name = payload.package.strip()
        if not name:
            return {"success": False, "error": "Package name cannot be blank."}
        return await asyncio.to_thread(install_package, settings.state_dir, name)

    @app.delete("/api/script-env/packages/{name}")
    async def remove_script_env_package(name: str) -> dict[str, object]:
        return await asyncio.to_thread(uninstall_package, settings.state_dir, name)

    @app.get("/api/script-env/interpreter")
    async def get_script_env_interpreter() -> dict[str, object]:
        """What ensure_script_env would try, in order, right now -- the
        Environment tab's manual override (if any) is already reflected
        first in `candidates` since the override changes what
        auto-detection itself returns; `auto_detected` is the plain
        fallback list on its own, filtered to candidates that actually
        run (see working_interpreters' docstring for why sys.executable
        specifically needs this on a packaged build) so the UI never
        offers a chip that's guaranteed to fail validation if clicked."""
        override = get_interpreter_override(settings.state_dir)
        candidates = [sys.executable, *fallbacks_for_platform()]
        return {
            "configured": override,
            "auto_detected": await asyncio.to_thread(working_interpreters, candidates),
        }

    @app.post("/api/script-env/interpreter")
    async def set_script_env_interpreter(payload: ScriptEnvInterpreterUpdate) -> dict[str, object]:
        return await asyncio.to_thread(
            set_interpreter_override, settings.state_dir, payload.path.strip() or None
        )

    # Mirrors the script-env endpoints above exactly, backed by
    # tools/node_env.py's npm-based node-env directory instead -- see that
    # module's docstring for why Node needs a different isolation
    # mechanism than the Python venv, and why node/npm being genuinely
    # optional (unlike Python, coscribe's own runtime) means these can
    # raise where the Python ones effectively never do in practice. Same
    # asyncio.to_thread reasoning as the script-env handlers above.
    @app.get("/api/node-env/packages")
    async def get_node_env_packages() -> list[dict[str, str]]:
        return await asyncio.to_thread(list_node_packages, settings.state_dir)

    @app.post("/api/node-env/packages")
    async def add_node_env_package(payload: ScriptEnvPackageInstall) -> dict[str, object]:
        name = payload.package.strip()
        if not name:
            return {"success": False, "error": "Package name cannot be blank."}
        return await asyncio.to_thread(install_node_package, settings.state_dir, name)

    @app.delete("/api/node-env/packages/{name}")
    async def remove_node_env_package(name: str) -> dict[str, object]:
        return await asyncio.to_thread(uninstall_node_package, settings.state_dir, name)

    @app.websocket("/ws/browser")
    async def browser_panel_ws(websocket: WebSocket) -> None:
        """Separate socket from /ws/{thread_id} on purpose, not new
        message types bolted onto that one -- screencast frames are a
        fundamentally different traffic shape (many small JPEGs a
        second, independent of any chat turn) from the chat protocol's
        own carefully-paced agent_delta/tool_result stream, and mixing
        them risks one starving the other. Not thread_id-scoped either:
        one browser_panel.py session for the lifetime of this one
        connection, closed the moment it drops -- see BrowserPanelSession's
        own docstring for why this is a companion tool, not conversation
        state.

        Registered *before* /ws/{thread_id} below on purpose -- Starlette
        matches WebSocket routes in registration order, and /ws/{thread_id}
        is a path-param route that would otherwise swallow /ws/browser
        first (thread_id="browser"), never reaching this handler at all.
        Confirmed the hard way: a route-order test written against a fake
        BrowserPanelSession failed with resolve_chat_model raising on the
        literal string "browser" as a model id, coming from _get_session --
        proof the request was landing in ws_endpoint instead."""
        await websocket.accept()
        session = BrowserPanelSession()
        try:
            await session.launch()
        except BrowserPanelError as exc:
            await websocket.send_json({"type": "error", "message": str(exc)})
            await websocket.close()
            return

        async def on_frame(data: str) -> None:
            await websocket.send_json({"type": "frame", "data": data})

        await session.start_screencast(on_frame)
        try:
            while True:
                data = await websocket.receive_json()
                message_type = data.get("type")
                try:
                    if message_type == "navigate":
                        await session.navigate(data["url"])
                    elif message_type == "reload":
                        await session.reload()
                    elif message_type == "back":
                        await session.go_back()
                    elif message_type == "forward":
                        await session.go_forward()
                    elif message_type == "mouse":
                        await session.dispatch_mouse(
                            data["kind"],
                            data["x"],
                            data["y"],
                            button=data.get("button", "left"),
                            delta_x=data.get("deltaX", 0),
                            delta_y=data.get("deltaY", 0),
                        )
                    elif message_type == "key":
                        await session.dispatch_key(data["kind"], data["key"])
                    elif message_type == "text":
                        await session.insert_text(data["text"])
                    elif message_type == "resize":
                        await session.resize(data["width"], data["height"], data.get("scale", 1.0))
                    elif message_type == "hover_element":
                        element = await session.hover_element(data["x"], data["y"])
                        await websocket.send_json({"type": "hover", "element": element})
                    elif message_type == "pick_element":
                        picked = await session.pick_element(data["x"], data["y"])
                        await websocket.send_json({"type": "picked", **picked})
                except BrowserPanelError as exc:
                    await websocket.send_json({"type": "error", "message": str(exc)})
        except WebSocketDisconnect:
            pass
        finally:
            await session.close()

    @app.websocket("/ws/{thread_id}")
    async def ws_endpoint(
        websocket: WebSocket,
        thread_id: str,
        skills: str | None = None,
        workspace: str | None = None,
    ) -> None:
        await websocket.accept()
        session = _get_session(thread_id, skills, workspace)
        try:
            await session.send_state(websocket)
            await session.send_history(websocket)
            # A pending approval from before a restart or dropped connection
            # doesn't wait for a new user_message to surface -- redeliver it
            # now. Backgrounded (not awaited) for the same reason
            # user_message handling is: it can block on a future that only
            # resolves via an approval_response arriving through the loop
            # below, so awaiting it inline here would deadlock.
            asyncio.create_task(session.resume_after_reconnect(websocket))
            while True:
                data = await websocket.receive_json()
                message_type = data.get("type")
                if message_type == "user_message":
                    asyncio.create_task(
                        session.handle_user_message(
                            data["text"], websocket, images=data.get("images")
                        )
                    )
                elif message_type == "edit_message":
                    # Same asyncio.create_task treatment as user_message
                    # above -- an edit runs a real turn afterward (may
                    # itself block on an approval), so it can't be awaited
                    # inline without blocking this loop from ever reaching
                    # the approval_response that would unblock it.
                    asyncio.create_task(
                        session.handle_edit_message(
                            data["index"], data["text"], websocket, images=data.get("images")
                        )
                    )
                elif message_type == "approval_response":
                    session.resolve_approval(data["id"], bool(data.get("approved")))
                elif message_type == "question_response":
                    session.resolve_question(data["id"], str(data.get("answer", "")))
                elif message_type == "stop":
                    session.request_stop()
                    await session.run_interrupt_hooks()
                elif message_type == "switch_model":
                    # Directly awaited, not asyncio.create_task like
                    # user_message -- same reasoning as web/app.py's
                    # identical handler: a model switch has no unbounded
                    # wait on a human the way an approval-blocked turn
                    # does, and Starlette's WebSocket.send() has no
                    # internal locking against concurrent callers.
                    await session.switch_model(data["model"], websocket)
                elif message_type == "select_skills":
                    # Multi-select, callable any number of times per thread
                    # -- no "already set" guard (see ChatSessionLG.
                    # set_enabled_skills' own docstring for why). Unknown
                    # names are dropped silently, same tolerance
                    # _resolve_enabled_skills gives them at connect time,
                    # rather than erroring the whole toggle over one bad
                    # entry.
                    requested = {n for n in data.get("skills", []) if n in _skills_by_name()}
                    await session.set_enabled_skills(requested, websocket)
                    _write_skills_sidecar(thread_id, requested)
                elif message_type == "select_workspace":
                    # In-app "new session" picker's path: the socket is
                    # already open by the time the user picks a folder (see
                    # ?workspace= on ws_endpoint above for the other,
                    # connect-time path), and ChatSessionLG.select_workspace
                    # itself guards against a second call on an
                    # already-explicit thread. Only persists the sidecar on
                    # success (True) -- see select_workspace's own
                    # docstring for why an unconditional write here would
                    # desync the sidecar from a rejected/failed switch.
                    if await session.select_workspace(data["path"], websocket):
                        _write_workspace_sidecar(thread_id, data["path"])
        except WebSocketDisconnect:
            # A turn still blocked on an approval for *this* connection at
            # the moment it drops would otherwise dangle forever: nothing
            # can ever resolve that pending Future once the socket that
            # would have carried its approval_response is gone, and
            # ChatSessionLG._turn_lock means an orphaned task like that
            # blocks every future turn on this thread_id too -- including
            # resume_after_reconnect's own attempt to redeliver the same
            # pending approval to a fresh connection. abandon_orphaned_turn
            # (not request_stop -- see its own docstring for why) hard-
            # cancels that task without ever resolving the approval or
            # resuming the graph, so the checkpointer's real pending state
            # is untouched and a genuine reconnect still redelivers it.
            session.abandon_orphaned_turn()
            await session.run_session_end_hooks()

    app.mount("/static", _NoCacheStaticFiles(directory=STATIC_DIR), name="static")

    return app


def _watch_parent_windows(parent_pid: int) -> None:
    """Windows half of _exit_when_orphaned -- there's no re-parenting
    signal to poll, so this blocks on a handle to the parent process and
    exits the moment it's actually signaled. Best-effort: this is
    defense-in-depth on top of office-agent-desktop's own
    RunEvent::Exit/RunEvent::ExitRequested child.kill(), which is the
    primary cleanup path regardless of whether this succeeds.

    Two correctness details, not obvious from the WinAPI docs alone:
    OpenProcess returns a 64-bit HANDLE, but ctypes defaults a function's
    return type to a 32-bit int -- without explicit restype/argtypes the
    handle gets silently truncated to garbage. And only WAIT_OBJECT_0
    means the parent genuinely terminated; a bad/invalid handle yields
    WAIT_FAILED immediately, and treating that the same as "parent died"
    would kill a perfectly healthy sidecar moments after it starts."""
    import ctypes
    import threading
    from ctypes import wintypes

    synchronize = 0x0010_0000
    wait_object_0 = 0x0000_0000

    # typeshed's ctypes stub only defines WinDLL under sys.platform == "win32" --
    # this repo's mypy always runs on Linux/macOS dev machines, where the stub
    # doesn't expose it at all, even though this function itself only ever runs
    # on real Windows (guarded by the caller's own sys.platform check above).
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    handle = kernel32.OpenProcess(synchronize, False, parent_pid)
    if not handle:
        return

    def watch() -> None:
        if kernel32.WaitForSingleObject(handle, 0xFFFF_FFFF) == wait_object_0:
            os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def _exit_when_orphaned() -> None:
    """When launched as office-agent-desktop's sidecar
    (COSCRIBE_EXIT_WITH_PARENT=1, set by the Electron shell -- or the
    now-legacy Tauri shell before it, which set the same flag -- when it
    spawns this process), exit if the parent dies -- even on an abrupt
    crash that skips the shell's own graceful kill of this process on
    quit. A no-op for the ordinary
    CLI/browser case (the env var is unset), and never runs at all
    unless a desktop shell opted in.

    COSCRIBE_PARENT_PID is the shell's own PID, passed explicitly
    rather than relying on os.getppid() alone. office-agent-desktop's
    packaging deliberately uses PyInstaller --onedir (see packaging/
    coscribe_server.spec's own docstring) specifically to avoid
    onefile's bootloader-in-the-middle problem, so getppid() would
    already point at the right process here -- but watching the
    explicit PID costs nothing and stays correct even if that changes.

    POSIX: poll the PID with kill(pid, 0) -- a liveness probe, no signal
    actually delivered. Windows has no equivalent, see
    _watch_parent_windows above."""
    if os.environ.get("COSCRIBE_EXIT_WITH_PARENT") != "1":
        return
    try:
        parent_pid = int(os.environ.get("COSCRIBE_PARENT_PID") or 0)
    except ValueError:
        parent_pid = 0
    parent_pid = parent_pid or os.getppid()

    if sys.platform == "win32":
        _watch_parent_windows(parent_pid)
        return

    import threading
    import time

    def watch() -> None:
        while True:
            time.sleep(1.5)
            try:
                os.kill(parent_pid, 0)
            except ProcessLookupError:
                os._exit(0)
            except PermissionError:
                pass  # alive, just owned by someone else -- keep watching

    threading.Thread(target=watch, daemon=True).start()


async def _run_web_server(host: str, port: int) -> None:
    """Runs the setup app (create_setup_app) until it has a usable
    Settings, then the real app (create_app_lg) -- both `uvicorn.Server`
    instances bound to the same host/port, one after the other, never at
    once. Managed by hand instead of the simpler `uvicorn.run(app, ...)`
    used before this existed, specifically so this same process/PID can
    serve two different apps in sequence -- see create_setup_app's own
    docstring for why an actual process restart isn't safe here."""
    settings = _load_settings_or_none()
    if settings is None:
        configured: asyncio.Future[Settings] = asyncio.get_running_loop().create_future()
        setup_server = uvicorn.Server(
            uvicorn.Config(create_setup_app(configured), host=host, port=port)
        )
        serve_task = asyncio.create_task(setup_server.serve())
        settings = await configured
        # Releases the port before the real app tries to bind it below --
        # sequential, not concurrent, so there's no double-bind to race.
        setup_server.should_exit = True
        await serve_task

    logging.basicConfig(level=settings.log_level)
    await uvicorn.Server(uvicorn.Config(create_app_lg(settings), host=host, port=port)).serve()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run coscribe's local web UI."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    # Not used by anything at runtime -- exists so the packaged desktop
    # shell's installer can execute this binary once, immediately after
    # extraction, purely to make it exit instantly instead of actually
    # starting a server (which would bind a port and run forever). Real,
    # live-reported problem this addresses: Windows Defender's real-time
    # scan of a large, unsigned, freshly-extracted exe on its very first
    # execution can take long enough to look like the app hung -- the
    # splash page's own 180s failure UI would fire, sidecar log
    # completely empty the whole time, every single fresh install/update
    # ("每次新包第一次运行都是要启动很久"). Triggering that same one-time
    # scan-and-cache cost during the install step (where a moment's delay
    # is already expected and shown as installer progress) instead of at
    # the user's first real launch fixes the *experience*, not the
    # underlying OS/AV cost -- see
    # office-agent-desktop-electron/build/installer.nsh, which is what
    # actually calls this.
    parser.add_argument("--version", action="version", version=f"coscribe {__version__}")
    args = parser.parse_args()

    _exit_when_orphaned()
    asyncio.run(_run_web_server(args.host, args.port))


if __name__ == "__main__":
    main()

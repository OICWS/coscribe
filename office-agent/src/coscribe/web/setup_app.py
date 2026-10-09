"""The first-run setup page and the tiny app that serves it, in place of the real
app until a model and its key have been saved."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import set_key
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..config import Settings
from ..runtime import env_value_for_storage, harden_file_permissions
from .provider_catalog import BUILTIN_PROVIDERS

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


def build_setup_app(
    configured: asyncio.Future[Settings],
    find_dotenv_path: Callable[[], Path],
    load_settings_or_none: Callable[[], Settings | None],
) -> FastAPI:
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
        dotenv_path = find_dotenv_path()
        set_key(
            str(dotenv_path),
            builtin["api_key_env"],
            env_value_for_storage(f"builtin-provider:{builtin['api_key_env']}", api_key),
        )
        set_key(str(dotenv_path), "COSCRIBE_DEFAULT_MODEL", f"{provider_key}:{model}")
        # Also write the per-provider default (COSCRIBE_GEMINI_DEFAULT_MODEL,
        # etc.) -- get_providers below reads *that* var to decide whether
        # this provider shows up in the model switcher at all, and first-run
        # setup used to only ever populate the global COSCRIBE_DEFAULT_MODEL,
        # leaving this one permanently unset. Real, live-reported bug: the
        # default provider (set up here) would vanish from the switcher's
        # own dropdown the instant the user switched to a second, properly-
        # configured provider and tried to switch back.
        set_key(str(dotenv_path), builtin["default_model_env"], model)
        harden_file_permissions(dotenv_path)

        settings = load_settings_or_none()
        if settings is None:
            return {
                "success": False,
                "error": "Saved, but coscribe still couldn't start -- double-check the model name.",
            }
        configured.set_result(settings)
        return {"success": True}

    return app

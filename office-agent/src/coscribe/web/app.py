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
import hmac
import html
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast

import uvicorn
from dotenv import dotenv_values, load_dotenv, set_key
from fastapi import Body, FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import ValidationError

from .. import __version__
from ..cli import _dotenv_path, _load_settings_or_none
from ..code_runtime.service import (
    remove_stale_code_sidecars,
    shutdown_code_services,
)
from ..config import Settings
from ..conversation.activity import OPENABLE_EXTENSIONS, open_in_os
from ..conversation.session import ChatSessionLG
from ..conversation.thread_meta import ThreadMetaStore
from ..coordinator import build_coordinator_agent
from ..runtime import (
    LLMClient,
    delete_secret,
    empty_hooks_config,
    env_resolve_secret_for_display,
    harden_file_permissions,
    load_custom_providers,
    load_hooks_config,
    resolve_env_keyring_refs,
    resolve_secret,
    run_hook,
    store_secret,
)
from ..runtime.secret_store import (
    SecretError,
    SecretStore,
    SessionEnvironments,
    placeholders_in,
)
from ..runtime.types import get_tool_metadata
from ..runtime_lg import (
    continue_workflow_run,
    execute_run,
    extract_text,
    poll_due_scheduled_tasks,
    poll_due_wakes,
    reconcile_interrupted_runs,
    start_investigation,
    stop_run,
    strip_mode_note,
)
from ..tools import (
    SkillInfo,
    load_builtin_skills,
    load_skills,
)
from ..tools.background_tasks import (
    BackgroundTaskStore,
    forget_finished_background_tasks,
    read_background_log,
    stop_background_task,
)
from ..tools.browser import BROWSER_HOST
from ..tools.connector_permissions import (
    POLICIES,
    ConnectorPermissions,
    apply_connector_permissions,
    connector_of,
    is_read_only,
    name_of,
)
from ..tools.mcp import (
    load_mcp_server_configs,
    prepare_for_connect,
    validate_mcp_config,
    with_secrets,
)
from ..tools.scheduled_tasks import (
    SCHEDULED_THREAD_PREFIX,
    ScheduledRun,
    ScheduledTriggerStore,
    compute_next_run_at,
    create_trigger,
    parse_run_thread_id,
    patch_trigger,
    record_draft,
    update_trigger,
)
from ..tools.skill_catalog import (
    enabled_skill_names,
)
from ..tools.subagent_tasks import (
    SubAgentTaskStore,
    forget_finished_subagents,
    get_subagent_transcript,
    stop_subagent_task,
)
from ..tools.tasks import TaskToolkit
from ..workflows.catalog import tool_description
from ..workflows.permissions import granted_by, summarize
from ..workflows.solidify import DraftFailed
from ..workflows.spec import BranchStep, LoopStep, parse_workflow, walk, workflow_error
from .background_events import BackgroundEvent, BackgroundEventBus, run_event
from .browser_panel import BrowserPanelError, BrowserPanelSession
from .connector_catalog import MCP_CATALOG, SeenTools
from .provider_catalog import (
    BUILTIN_PROVIDERS,
)
from .routes.files import router as files_router
from .routes.settings import router as settings_router
from .routes.shared import _mask, _read_mcp_servers_raw, _read_providers_raw
from .routes.skills import router as skills_router
from .schemas import (
    BrowserHostReply,
    ConnectorPermissionsUpdate,
    GroupRename,
    InvestigateRequest,
    MCPServerUpdate,
    MCPVersionBump,
    OAuthAppCredentials,
    OAuthAppImport,
    OpenFileRequest,
    PermissionsRequest,
    RunNowRequest,
    ScheduledTaskCreate,
    TaskNotesUpdate,
    ThreadMetaPatch,
    ThreadRename,
    WorkflowAnswer,
    WorkflowCheck,
    WorkflowDraftRequest,
    WorkflowRetry,
)
from .setup_app import build_setup_app
from .state import AppState

if TYPE_CHECKING:
    # Real type only needed for a local variable annotation below (never
    # evaluated at runtime -- `from __future__ import annotations` is in
    # effect) -- kept out of the real import graph so `import coscribe.
    # web.app` doesn't drag in langchain_mcp_adapters/mcp for a user with
    # no MCP servers configured. See connect_mcp_tools_lg/connect_one_mcp_
    # server_lg below for the same reasoning applied to the functions that
    # actually need this module at runtime.
    from ..runtime_lg.mcp import McpServerConnection

def _oauth_page(message: str) -> str:
    """The page a connector's sign-in redirects the browser to."""
    return (
        "<!doctype html><meta charset=utf-8><title>coscribe</title>"
        "<body style=\"font-family:system-ui,sans-serif;max-width:32rem;margin:20vh auto;"
        f"padding:0 1rem\"><h2>coscribe</h2><p>{html.escape(message)}</p></body>"
    )
# How long lifespan() blocks app startup on connect_mcp_tools_lg before
# letting a still-connecting server finish in the background instead --
# see lifespan's own comment. Same default Claude Code itself settled on
# (MCP_CONNECT_TIMEOUT_MS) for the identical problem.
MCP_STARTUP_TIMEOUT_SECONDS = 5.0


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


# A small, curated, hardcoded list -- not a live marketplace. Every entry is
# a hosted (remote) MCP server the user signs in to in their own browser:
# nothing is installed, no app is registered, and no company IT step is
# needed, so it works wherever the person can already open the service
# themselves. Each one was checked against the live server (2026-10-07): the
# registration call is accepted and the sign-in page is reached. A server that
# needs a pre-registered app -- Slack, Google Workspace, Box, HubSpot, and
# Dropbox, which lists a registration address but answers "only pre-registered
# trusted partners" -- or a company-approved app, like Microsoft 365 in a
# tenant that doesn't allow third-party apps, is left out on purpose.
# Anything else is still addable by hand from the Custom tab.
# git/github catalog entries (local git ops via mcp-server-git; GitHub
# issues/PRs/repo search via GitHub's own remote MCP server + an OAuth
# Device Flow sign-in) existed here and were removed -- coscribe's target
# user is a general office file/task automation assistant, not a developer
# tool. The Microsoft 365 entry (a community server run locally with npx,
# signing in by device code) was removed for the reasons above. Still
# addable by hand via the Connectors panel's Custom tab.

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

def create_setup_app(configured: asyncio.Future[Settings]) -> FastAPI:
    return build_setup_app(configured, _dotenv_path, _load_settings_or_none)


def _mask_value(value: str) -> str:
    """A connector value for display. One that refers to a secret is shown
    as written, placeholder and all, with any other text of it hidden (short
    pieces such as "Bearer " stay readable): the value itself is never in it."""
    if not placeholders_in(value):
        return _mask(value)
    parts = re.split(r"(\{\{secret:[A-Za-z_][A-Za-z0-9_]{0,63}\}\})", value)
    return "".join(
        part if placeholders_in(part) or len(part) <= 7 else "*" * len(part) for part in parts
    )


# Plain name ("mcp-server-fetch") or scoped ("@playwright/mcp") npm package
# name -- deliberately doesn't allow anything npm view could misparse as a
# flag (e.g. a leading "-"), since `package` here comes straight from the
# browser.
_NPM_PACKAGE_NAME_RE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")


class _NoSocket:
    """Stands in for a websocket where nobody is listening."""

    async def send_json(self, data: dict[str, Any]) -> None:
        return None


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
    meta_store = ThreadMetaStore(settings.state_dir)
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
    # Deferred import for the same reason as runtime_lg.mcp (see the comment
    # above MCP_STARTUP_TIMEOUT_SECONDS).
    from ..runtime_lg.mcp_oauth import (
        APP_REDIRECT_URI,
        SIGN_IN_TIMEOUT_SECONDS,
        CallbackPortBusy,
        LoopbackCallback,
        McpOAuth,
        NeedsSignIn,
    )

    mcp_oauth = McpOAuth(settings.state_dir)

    def _oauth_callback_response(
        state: str, code: str | None, error: str | None
    ) -> tuple[int, str]:
        name = mcp_oauth.complete(state, code, error)
        if name is None:
            return 400, _oauth_page("This sign-in link has expired. Start again from coscribe.")
        if error:
            return 200, _oauth_page(f"Signing in to {name} didn't work: {error}.")
        return 200, _oauth_page(
            f"You're signed in to {name}. You can close this tab and go back to coscribe."
        )

    app_callback = LoopbackCallback(_oauth_callback_response)
    sign_in_tasks: set[asyncio.Task[None]] = set()

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

    def _write_workspace_sidecar(thread_id: str, folders: list[str]) -> None:
        if not folders:
            _workspace_sidecar_path(thread_id).unlink(missing_ok=True)
            return
        settings.state_dir.mkdir(parents=True, exist_ok=True)
        _workspace_sidecar_path(thread_id).write_text(json.dumps(folders), encoding="utf-8")

    def _read_workspace_sidecar(thread_id: str) -> list[str]:
        sidecar = _workspace_sidecar_path(thread_id)
        if not sidecar.is_file():
            return []
        text = sidecar.read_text(encoding="utf-8").strip()
        # Threads from before multi-folder conversations hold one bare path.
        if not text.startswith("["):
            return [text] if text else []
        try:
            saved = json.loads(text)
        except json.JSONDecodeError:
            return []
        return [str(folder) for folder in saved if isinstance(folder, str) and folder]

    def _run_workspace(thread_id: str) -> str | None:
        """A scheduled run's thread works in its task's folder."""
        parsed = parse_run_thread_id(thread_id)
        if parsed is None:
            return None
        trigger = ScheduledTriggerStore(settings.state_dir).load(parsed[0])
        return trigger.workspace if trigger is not None else None

    def _run_folders(thread_id: str) -> list[str]:
        """A scheduled run's folders: its task's own, then the existing
        ones its workflow names or the person allowed while a run waited."""
        parsed = parse_run_thread_id(thread_id)
        trigger = ScheduledTriggerStore(settings.state_dir).load(parsed[0]) if parsed else None
        if trigger is None:
            return []
        extra = list(trigger.permissions.get("folders", []))
        if trigger.workflow is not None:
            extra = [*granted_by(parse_workflow(trigger.workflow))["folders"], *extra]
        extra = [folder for folder in dict.fromkeys(extra) if Path(folder).is_dir()]
        if not extra:
            return [trigger.workspace] if trigger.workspace else []
        return [trigger.workspace or str(settings.workspace_root), *extra]

    def _resolve_folders(thread_id: str, workspace_param: str | None) -> list[Path]:
        """The ?workspace= query param only counts the first time a thread
        is seen; after that the sidecar (changed through set_folders) wins.
        Empty means the app's default workspace -- kept distinct from an
        explicit choice of that same folder, which the UI shows as chosen."""
        if _workspace_sidecar_path(thread_id).is_file():
            return [Path(folder) for folder in _read_workspace_sidecar(thread_id)]
        run_folders = _run_folders(thread_id)
        if run_folders and workspace_param is None:
            _write_workspace_sidecar(thread_id, run_folders)
            return [Path(folder) for folder in run_folders]
        if workspace_param is None:
            workspace_param = _run_workspace(thread_id)
        if not workspace_param:
            return []
        _write_workspace_sidecar(thread_id, [workspace_param])
        return [Path(workspace_param)]

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

    def _enabled_skills() -> set[str]:
        # One global on/off per skill (Settings > Skills), read fresh so a
        # toggle applies to the next session without a restart.
        return enabled_skill_names(settings.skills_dir, settings.state_dir)

    def _get_session(
        thread_id: str,
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
            folders = _resolve_folders(thread_id, workspace_param)
            sessions[thread_id] = ChatSessionLG(
                thread_id=thread_id,
                settings=settings,
                context_window_client=context_window_client,
                custom_providers=session_custom_providers,
                extra_tools=_session_extra_tools(),
                checkpointer=checkpointer_holder["checkpointer"],
                hooks_config=hooks_config,
                enabled_skill_names=_enabled_skills(),
                workspace_root=folders[0] if folders else settings.workspace_root,
                workspace_explicit=bool(folders),
                extra_folders=folders[1:],
                configured_models=_configured_models,
            )
        return sessions[thread_id]

    def _session_extra_tools() -> list[Any]:
        """The connector tools conversations get, under the permissions set
        in Settings > Connectors."""
        return apply_connector_permissions(
            extra_tools_holder["tools"], ConnectorPermissions(settings.state_dir).load()
        )

    def _connector_tools(name: str) -> list[dict[str, Any]]:
        permissions = ConnectorPermissions(settings.state_dir).load().get(name, {})
        prefix = f"{name}_"
        result = []
        for tool in extra_tools_holder["tools"]:
            if connector_of(tool) != name:
                continue
            full_name = name_of(tool)
            bare = full_name[len(prefix) :] if full_name.startswith(prefix) else full_name
            title = (getattr(tool, "metadata", None) or {}).get("title") or (
                bare.replace("-", " ").replace("_", " ").strip().capitalize()
            )
            result.append(
                {
                    "name": full_name,
                    "title": title,
                    "description": tool_description(tool).split(". ")[0],
                    "read_only": is_read_only(tool),
                    "policy": permissions.get(full_name, "ask"),
                }
            )
        return result

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

    reconnecting: set[str] = set()

    def _reconnect_stale_connector(name: str) -> None:
        """A connector call hung or hit a dead session: connect it afresh in
        the background, once at a time per connector."""
        if name in reconnecting:
            return
        reconnecting.add(name)

        async def reconnect() -> None:
            try:
                if settings.mcp_config_path is None or not settings.mcp_config_path.is_file():
                    return
                entry = _read_mcp_servers_raw(settings.mcp_config_path)["mcpServers"].get(name)
                if entry is None:
                    return
                config = validate_mcp_config({"type": "mcp", "name": name, **entry})
                await _disconnect_mcp_server_lg(name)
                await _connect_and_register_mcp_server_lg(name, config)
                await _refresh_all_sessions_extra_tools()
            except Exception:  # noqa: BLE001 -- the Connectors page shows it as not connected
                logging.getLogger(__name__).warning(
                    "Reconnecting connector %r failed", name, exc_info=True
                )
            finally:
                reconnecting.discard(name)

        task = asyncio.create_task(reconnect())
        sign_in_tasks.add(task)
        task.add_done_callback(sign_in_tasks.discard)

    async def _connect_and_register_mcp_server_lg(
        name: str, config: Any, connect_timeout: float | None = None
    ) -> tuple[bool, str | None]:
        """Connects one MCP server and registers its tools into
        extra_tools_holder. Returns (connected, error) -- error is a
        human-readable failure reason from connect_one_mcp_server_lg, or
        None on success. connect_one_mcp_server_lg's only positive-connect
        signal is a non-empty tool list, same imprecision as treating a
        real server that happens to expose zero tools as "didn't connect";
        accepted here since real MCP servers always expose at least one
        tool in practice."""
        # Deferred import -- see the top-of-file comment above MCP_STARTUP_TIMEOUT_SECONDS.
        from ..runtime_lg.mcp import connect_one_mcp_server_lg

        try:
            config = prepare_for_connect(config, settings.state_dir)
        except (SecretError, RuntimeError) as exc:
            return False, str(exc)
        extra = {} if connect_timeout is None else {"connect_timeout": connect_timeout}
        new_tools, connection, error = await connect_one_mcp_server_lg(
            name, config, mcp_oauth.auth_for, on_stale=_reconnect_stale_connector, **extra
        )
        if connection is None:
            return False, error
        if not new_tools:
            # Connected, but a connector with no tools is no use, and its
            # session would otherwise stay open with nothing to close it.
            await connection.close()
            return False, "The server connected but offers no tools."
        extra_tools_holder["tools"].extend(new_tools)
        mcp_connections[name] = connection
        return True, None

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
        tools = _session_extra_tools()
        for session in sessions.values():
            await session.refresh_extra_tools(tools)

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
                    # poll_due_wakes already resumed wake.thread_id's own
                    # checkpointer correctly (via SilentSocket) -- this
                    # is the other half, nudging a *live* browser tab (if
                    # any) that was already open on that thread to go
                    # re-fetch it, closing the real, previously-documented
                    # gap where a poller-triggered resume wrote correctly
                    # but never appeared in an already-open tab until its
                    # next reload/reconnect. A no-op if no tab is open on
                    # that thread right now (see notify_resync's own
                    # docstring).
                    live_session = sessions.get(wake.thread_id)
                    if live_session is not None:
                        await live_session.notify_resync()
            except Exception:
                logging.getLogger(__name__).exception("selfwake: poll_due_wakes failed")
            # Backgrounded rather than awaited: a run can take minutes, and
            # due wakes must keep being checked meanwhile. Overlapping polls
            # can't double-fire a task -- its schedule advances before the
            # run's first await (see poll_due_scheduled_tasks).
            _track_background(asyncio.create_task(_poll_scheduled_tasks_once()))

    async def _poll_scheduled_tasks_once() -> None:
        try:
            fired_triggers = await poll_due_scheduled_tasks(
                settings.state_dir, _get_session_async, on_pruned=_delete_run_threads
            )
        except Exception:
            logging.getLogger(__name__).exception(
                "scheduled_tasks: poll_due_scheduled_tasks failed"
            )
            return
        for trigger in fired_triggers:
            if trigger.runs:
                await _announce_run(trigger.trigger_id, trigger.runs[-1])

    async def _announce_run(trigger_id: str, run: ScheduledRun | None) -> None:
        """Tell the desktop app how a run ended -- and, for a failed run of a
        task set to, start a conversation looking into it first, so the
        notification can open that."""
        if run is None:
            return
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        if trigger is None:
            return
        investigation = None
        if (
            run.status == "failed"
            and trigger.auto_investigate
            and trigger.workflow is not None
            and run.investigation is None
        ):
            investigation = uuid.uuid4().hex[:8]
            _track_background(
                asyncio.create_task(
                    start_investigation(
                        settings.state_dir,
                        trigger_id,
                        run.run_id,
                        investigation,
                        None,
                        _get_session_async,
                        attended=False,
                    )
                )
            )
        event = run_event(trigger, run, investigation)
        if event is not None:
            background_events.publish(event)

    async def _execute_and_announce(trigger_id: str, run_id: str) -> None:
        run = await execute_run(settings.state_dir, trigger_id, run_id, _get_session_async)
        await _announce_run(trigger_id, run)

    background_tasks: set[asyncio.Task[Any]] = set()

    def _track_background(task: asyncio.Task[Any]) -> None:
        # The event loop only holds weak references to tasks -- without a
        # strong one here, a run could be garbage-collected mid-flight.
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    async def _delete_thread_data(thread_id: str) -> bool:
        """Delete a thread's checkpoints plus every sidecar file that belongs
        to it -- otherwise a new thread later reusing the same id would
        inherit an old task list or workspace/skills choice. Returns
        whether any checkpoint existed."""
        checkpointer = checkpointer_holder["checkpointer"]
        await checkpointer.setup()  # see list_threads' identical comment
        cursor = await checkpointer.conn.execute(
            "SELECT 1 FROM checkpoints WHERE thread_id = ? LIMIT 1", (thread_id,)
        )
        existed = await cursor.fetchone() is not None
        await checkpointer.adelete_thread(thread_id)
        (settings.state_dir / f"{thread_id}.tasks.json").unlink(missing_ok=True)
        _workspace_sidecar_path(thread_id).unlink(missing_ok=True)
        _title_sidecar_path(thread_id).unlink(missing_ok=True)
        SessionEnvironments(settings.state_dir).delete(thread_id)
        ThreadMetaStore(settings.state_dir).delete(thread_id)
        sessions.pop(thread_id, None)
        return existed

    async def _delete_run_threads(runs: list[ScheduledRun]) -> None:
        for run in runs:
            await _delete_thread_data(run.thread_id)

    async def _sweep_orphaned_run_threads() -> int:
        """Delete run conversations no task still lists -- left behind by
        paths with no checkpointer to delete through (the model's
        delete_scheduled_task, `coscribe --check-wakes` pruning old runs).
        Thread ids are read before the store: a run is recorded before its
        conversation exists, so one starting concurrently (a CLI poll) is
        never mistaken for an orphan."""
        checkpointer = checkpointer_holder["checkpointer"]
        await checkpointer.setup()  # see list_threads' identical comment
        cursor = await checkpointer.conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ?",
            (f"{SCHEDULED_THREAD_PREFIX}%",),
        )
        thread_ids = [row[0] for row in await cursor.fetchall()]
        known = {
            run.thread_id
            for trigger in ScheduledTriggerStore(settings.state_dir).list_all()
            for run in trigger.runs
        }
        orphans = [t for t in thread_ids if t not in known]
        for thread_id in orphans:
            await _delete_thread_data(thread_id)
        return len(orphans)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        remove_stale_code_sidecars(settings.state_dir)
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
                    connect_mcp_tools_lg(
                        settings.mcp_config_path,
                        mcp_oauth.auth_for,
                        _reconnect_stale_connector,
                        state_dir=settings.state_dir,
                    )
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
            interrupted = reconcile_interrupted_runs(settings.state_dir)
            if interrupted:
                logging.getLogger(__name__).warning(
                    "Marked %d scheduled run(s) as failed -- still running at startup, "
                    "left over from a previous process that didn't shut down cleanly.",
                    interrupted,
                )
            swept = await _sweep_orphaned_run_threads()
            if swept:
                logging.getLogger(__name__).info(
                    "Deleted %d scheduled-run conversation(s) whose task or run record is gone.",
                    swept,
                )
            wake_poll_task = asyncio.create_task(_wake_poll_loop())
            yield
            wake_poll_task.cancel()
            try:
                await wake_poll_task
            except asyncio.CancelledError:
                pass
            for task in list(background_tasks):
                task.cancel()
            await asyncio.gather(*background_tasks, return_exceptions=True)
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
            for task in list(sign_in_tasks):
                task.cancel()
            await app_callback.stop()
            await shutdown_code_services()

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

    def _is_browser_host(request: Request) -> bool:
        expected = settings.browser_host_token
        given = request.headers.get("x-coscribe-browser-token", "")
        return bool(expected) and hmac.compare_digest(given.encode(), str(expected).encode())

    @app.get("/internal/browser-host", response_model=None)
    async def browser_host_stream(request: Request) -> Response:
        """The desktop app's browser takes its commands from here; see
        tools/browser.py."""
        if not _is_browser_host(request):
            return JSONResponse({"error": "Not the desktop app's browser."}, status_code=403)
        outbox = BROWSER_HOST.attach()

        async def command_source() -> AsyncIterator[str]:
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        command = await asyncio.wait_for(outbox.get(), 15)
                    except TimeoutError:
                        # A write is the only way a dropped connection gets
                        # noticed, and commands can be minutes apart.
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(command)}\n\n"
            finally:
                BROWSER_HOST.detach(outbox)

        return StreamingResponse(command_source(), media_type="text/event-stream")

    @app.post("/internal/browser-host/result")
    async def browser_host_result(request: Request, reply: BrowserHostReply) -> JSONResponse:
        if not _is_browser_host(request):
            return JSONResponse({"error": "Not the desktop app's browser."}, status_code=403)
        BROWSER_HOST.resolve(reply.id, reply.model_dump())
        return JSONResponse({"ok": True})

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
        # A fired Scheduled Task's own dedicated conversation has its own
        # surface (the frontend's Scheduled portal/detail/chat view) --
        # excluded here so it doesn't also show up in the ordinary chat
        # sidebar's session list, which would otherwise happen the moment
        # a trigger's first turn writes a checkpoint under its thread_id.
        rows = [row for row in rows if not row[0].startswith(SCHEDULED_THREAD_PREFIX)]
        waiting = await _threads_waiting_for_input()
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
            workspace_root = next(
                iter(_read_workspace_sidecar(thread_id)), str(settings.workspace_root)
            )
            title_sidecar = _title_sidecar_path(thread_id)
            if title_sidecar.is_file():
                preview = title_sidecar.read_text(encoding="utf-8").strip()
            meta = meta_store.get(thread_id)
            summaries.append(
                {
                    "thread_id": thread_id,
                    "updated_at": tuple_.checkpoint["ts"] if tuple_ else None,
                    "message_count": len(messages),
                    "preview": preview[:200],
                    "workspace_root": workspace_root,
                    "group": meta["group"],
                    "archived": meta["archived"],
                    "status": _thread_status(thread_id, waiting, meta),
                }
            )
        summaries.sort(key=lambda s: s["updated_at"] or "", reverse=True)
        return summaries

    async def _threads_waiting_for_input() -> set[str]:
        """Threads whose latest checkpoint holds an unanswered interrupt: an
        approval, a question or a plan waiting on a person -- also after a
        restart, when no session is in memory."""
        checkpointer = checkpointer_holder["checkpointer"]
        await checkpointer.setup()  # see list_threads' identical comment
        cursor = await checkpointer.conn.execute(
            "SELECT DISTINCT w.thread_id FROM writes w JOIN "
            "(SELECT thread_id, MAX(checkpoint_id) AS cid FROM checkpoints "
            "WHERE checkpoint_ns = '' GROUP BY thread_id) c "
            "ON w.thread_id = c.thread_id AND w.checkpoint_id = c.cid "
            "WHERE w.checkpoint_ns = '' AND w.channel = '__interrupt__'"
        )
        return {row[0] for row in await cursor.fetchall()}

    def _thread_status(thread_id: str, waiting: set[str], meta: dict[str, Any]) -> str:
        """"needs_input" | "working" | "ready" | "idle"."""
        session = sessions.get(thread_id)
        if thread_id in waiting:
            return "needs_input"
        if session is not None and session.turn_running:
            return "working"
        return "ready" if meta["unseen"] else "idle"

    @app.get("/api/threads/status")
    async def threads_status() -> dict[str, Any]:
        """Just each conversation's status, cheap enough to poll."""
        waiting = await _threads_waiting_for_input()
        known = set(sessions) | waiting
        known |= {p.name.removesuffix(".meta.json") for p in settings.state_dir.glob("*.meta.json")}
        return {
            "statuses": {
                thread_id: _thread_status(thread_id, waiting, meta_store.get(thread_id))
                for thread_id in known
                if not thread_id.startswith(SCHEDULED_THREAD_PREFIX)
            }
        }

    @app.get("/api/thread-groups")
    async def thread_groups() -> dict[str, Any]:
        return {"groups": meta_store.groups()}

    @app.post("/api/threads/{thread_id}/meta")
    async def patch_thread_meta(thread_id: str, payload: ThreadMetaPatch) -> JSONResponse:
        fields: dict[str, Any] = {}
        if payload.group is not None:
            try:
                named = payload.group.strip()
                fields["group"] = meta_store.add_group(named) if named else None
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
        if payload.archived is not None:
            fields["archived"] = payload.archived
        meta = meta_store.update(thread_id, **fields)
        return JSONResponse({"thread_id": thread_id, **meta, "groups": meta_store.groups()})

    @app.post("/api/thread-groups/{name}/rename")
    async def rename_thread_group(name: str, payload: GroupRename) -> JSONResponse:
        try:
            renamed = meta_store.rename_group(name, payload.name)
        except KeyError:
            return JSONResponse({"error": f"No group {name!r}"}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"name": renamed, "groups": meta_store.groups()})

    @app.delete("/api/thread-groups/{name}")
    async def delete_thread_group(name: str) -> JSONResponse:
        try:
            meta_store.delete_group(name)
        except KeyError:
            return JSONResponse({"error": f"No group {name!r}"}, status_code=404)
        return JSONResponse({"groups": meta_store.groups()})

    @app.delete("/api/threads/{thread_id}")
    async def delete_thread(thread_id: str) -> JSONResponse:
        existed = await _delete_thread_data(thread_id)
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

    @app.get("/api/threads/{thread_id}/activity")
    async def get_thread_activity(thread_id: str) -> dict[str, Any]:
        session = _get_session(thread_id)
        activity = await session.get_activity()
        return {"tasks": TaskToolkit(thread_id, settings.state_dir).list_tasks(), **activity}

    @app.post("/api/threads/{thread_id}/workflow-draft")
    async def draft_thread_workflow(
        thread_id: str, body: WorkflowDraftRequest | None = None
    ) -> JSONResponse:
        session = await _get_session_async(thread_id)
        try:
            draft = await session.draft_workflow((body.name if body else "").strip())
        except DraftFailed as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(draft.to_dict())

    @app.post("/api/workflows/validate")
    async def validate_workflow(body: WorkflowCheck) -> JSONResponse:
        try:
            workflow = parse_workflow(body.workflow)
        except ValidationError as exc:
            return JSONResponse({"error": workflow_error(exc, body.workflow)}, status_code=400)
        return JSONResponse({"workflow": workflow.model_dump(mode="json")})

    def _tool_risks() -> dict[str, str]:
        risks: dict[str, str] = {}
        for connector_tool in _session_extra_tools():
            metadata = get_tool_metadata(connector_tool)
            if (metadata.category or "").startswith("mcp:"):
                risks[connector_tool.name] = metadata.risk_category
        for tool in build_coordinator_agent(settings, thread_id="__tools_probe__").tools:
            risks[tool.__name__] = get_tool_metadata(tool).risk_category
        return risks

    @app.post("/api/workflows/permissions")
    async def workflow_permissions(body: PermissionsRequest) -> JSONResponse:
        """What a workflow will touch -- shown where it's reviewed -- and,
        given an earlier version, what this one adds to it."""
        try:
            workflow = parse_workflow(body.workflow)
            earlier = parse_workflow(body.previous) if body.previous is not None else None
        except ValidationError as exc:
            return JSONResponse({"error": workflow_error(exc, body.workflow)}, status_code=400)
        trigger = (
            ScheduledTriggerStore(settings.state_dir).load(body.trigger_id)
            if body.trigger_id
            else None
        )
        granted = trigger.permissions if trigger is not None else None
        if earlier is None and body.against_saved and trigger is not None and trigger.workflow:
            earlier = parse_workflow(trigger.workflow)
        risks = _tool_risks()
        summary = summarize(workflow, risks, granted)
        added: dict[str, Any] | None = None
        if earlier is not None:
            before = summarize(earlier, risks, granted)
            added = {
                "folders": [f for f in summary["folders"] if f not in before["folders"]],
                "sites": [x for x in summary["sites"] if x not in before["sites"]],
                "scripts": [t for t in summary["scripts"] if t not in before["scripts"]],
                "notable": [
                    n
                    for n in summary["notable"]
                    if n["tool"] not in {b["tool"] for b in before["notable"]}
                ],
            }
        return JSONResponse({"permissions": summary, "added": added})

    def _thread_file(thread_id: str, path: str) -> Path | None:
        try:
            resolved = _get_session(thread_id).workspace_scope().resolve(path)
        except (PermissionError, OSError, ValueError):
            return None
        return resolved if resolved.is_file() else None

    @app.post("/api/threads/{thread_id}/files/open")
    async def open_thread_file(thread_id: str, body: OpenFileRequest) -> JSONResponse:
        resolved = _thread_file(thread_id, body.path)
        if resolved is None:
            return JSONResponse({"error": f"No file {body.path!r}"}, status_code=404)
        if not body.reveal and resolved.suffix.lower() not in OPENABLE_EXTENSIONS:
            return JSONResponse(
                {"error": f"{resolved.suffix or 'This'} files can't be opened from here"},
                status_code=400,
            )
        try:
            open_in_os(resolved, reveal=body.reveal)
        except FileNotFoundError:
            return JSONResponse(
                {"error": "No app on this machine can open it -- download it instead."},
                status_code=501,
            )
        except OSError as exc:
            return JSONResponse({"error": f"Couldn't open it: {exc}"}, status_code=501)
        return JSONResponse({"opened": body.path})

    @app.get("/api/threads/{thread_id}/files/download")
    async def download_thread_file(thread_id: str, path: str) -> Response:
        resolved = _thread_file(thread_id, path)
        if resolved is None:
            return JSONResponse({"error": f"No file {path!r}"}, status_code=404)
        return FileResponse(resolved, filename=resolved.name)

    @app.get("/api/threads/{thread_id}/context-breakdown")
    async def get_context_breakdown_endpoint(thread_id: str) -> dict[str, Any]:
        # Deliberately reuses _get_session (the same live-session cache
        # every WS-driven call goes through), not a fresh throwaway
        # ChatSessionLG -- the whole point is reporting *this* thread's
        # real, currently-bound tool/skill/MCP set and its actual last
        # usage report, not a generic recomputation from scratch.
        session = _get_session(thread_id)
        return await session.get_context_breakdown()

    # -- Sub Agents panel. Reads are polled; a running session also nudges
    # the tab with "subagents_changed". Stopping is the user's call, so it's
    # a REST action here, not a model tool.

    @app.get("/api/threads/{thread_id}/subagents")
    async def list_subagents_endpoint(thread_id: str) -> list[dict[str, Any]]:
        tasks = SubAgentTaskStore(settings.state_dir).list_for_thread(thread_id)
        return [t.to_dict() for t in tasks]

    @app.get("/api/subagents/{task_id}/transcript")
    async def get_subagent_transcript_endpoint(task_id: str) -> JSONResponse:
        task = SubAgentTaskStore(settings.state_dir).load(task_id)
        if task is None:
            return JSONResponse({"error": f"No sub-agent task {task_id!r}"}, status_code=404)
        transcript = get_subagent_transcript(task_id)
        entries = (transcript or {}).get("entries", [])
        return JSONResponse({"task": task.to_dict(), "entries": entries})

    @app.post("/api/subagents/{task_id}/stop")
    async def stop_subagent_endpoint(task_id: str) -> JSONResponse:
        try:
            return JSONResponse(stop_subagent_task(settings.state_dir, task_id))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.delete("/api/threads/{thread_id}/subagents")
    async def forget_finished_subagents_endpoint(thread_id: str) -> dict[str, int]:
        return {"removed": forget_finished_subagents(settings.state_dir, thread_id)}

    # Background scripts share the panel with sub-agents; the same four
    # actions, over their own store.

    @app.get("/api/threads/{thread_id}/background-tasks")
    async def list_background_tasks_endpoint(thread_id: str) -> list[dict[str, Any]]:
        tasks = BackgroundTaskStore(settings.state_dir).list_for_thread(thread_id)
        return [t.to_dict() for t in tasks]

    @app.get("/api/background-tasks/{task_id}/log")
    async def get_background_task_log_endpoint(task_id: str) -> JSONResponse:
        log = read_background_log(settings.state_dir, task_id)
        if log is None:
            return JSONResponse({"error": f"No background task {task_id!r}"}, status_code=404)
        return JSONResponse(log)

    @app.post("/api/background-tasks/{task_id}/stop")
    async def stop_background_task_endpoint(task_id: str) -> JSONResponse:
        try:
            return JSONResponse(stop_background_task(settings.state_dir, task_id))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @app.delete("/api/threads/{thread_id}/background-tasks")
    async def forget_finished_background_tasks_endpoint(thread_id: str) -> dict[str, int]:
        return {"removed": forget_finished_background_tasks(settings.state_dir, thread_id)}

    # -- /api/scheduled-tasks -- the Settings > Scheduled Tasks panel's
    # create-without-a-conversation entry point; direct ScheduledTriggerStore
    # reads/writes, no session/graph involved. POST reuses create_trigger
    # (tools/scheduled_tasks.py) -- the exact same validation
    # create_scheduled_task (the model tool) uses, so the two creation
    # paths can't silently drift apart.

    @app.get("/api/scheduled-tasks")
    async def list_scheduled_tasks_endpoint() -> list[dict[str, Any]]:
        return [t.to_dict() for t in ScheduledTriggerStore(settings.state_dir).list_all()]

    @app.post("/api/scheduled-tasks")
    async def create_scheduled_task_endpoint(payload: ScheduledTaskCreate) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        try:
            trigger = create_trigger(
                store,
                name=payload.name,
                kind=payload.kind,
                at=payload.at,
                prompt=payload.prompt,
                weekday=payload.weekday,
                day_of_month=payload.day_of_month,
                start_date=payload.start_date,
                model=payload.model,
                approval_mode=payload.approval_mode,
                notes_enabled=payload.notes_enabled,
                workflow=payload.workflow,
                workspace=payload.workspace,
            )
        except (ValueError, KeyError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        if payload.from_thread:
            trigger.source_thread = payload.from_thread
            store.save(trigger)
        if payload.from_draft:
            record_draft(store, trigger, payload.from_draft)
        return JSONResponse(trigger.to_dict())

    @app.put("/api/scheduled-tasks/{trigger_id}")
    async def update_scheduled_task_endpoint(
        trigger_id: str, payload: ScheduledTaskCreate
    ) -> JSONResponse:
        try:
            trigger = update_trigger(
                ScheduledTriggerStore(settings.state_dir),
                trigger_id,
                name=payload.name,
                kind=payload.kind,
                at=payload.at,
                prompt=payload.prompt,
                weekday=payload.weekday,
                day_of_month=payload.day_of_month,
                start_date=payload.start_date,
                model=payload.model,
                approval_mode=payload.approval_mode,
                notes_enabled=payload.notes_enabled,
                workflow=payload.workflow,
                workspace=payload.workspace,
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(trigger.to_dict())

    @app.patch("/api/scheduled-tasks/{trigger_id}")
    async def patch_scheduled_task_endpoint(
        trigger_id: str, changes: Annotated[dict[str, Any], Body()]
    ) -> JSONResponse:
        try:
            trigger = patch_trigger(ScheduledTriggerStore(settings.state_dir), trigger_id, changes)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(trigger.to_dict())

    @app.post("/api/scheduled-tasks/{trigger_id}/run")
    async def run_scheduled_task_now_endpoint(
        trigger_id: str, payload: RunNowRequest | None = None
    ) -> JSONResponse:
        # Returns as soon as the run is recorded, not when it finishes: the
        # browser opens the run's own conversation right away and watches
        # it stream there (see runtime_lg/scheduled_tasks.py's
        # _RelaySocket). How it ended lands on the run record.
        store = ScheduledTriggerStore(settings.state_dir)
        try:
            trigger, run, pruned = store.start_run(
                trigger_id, "manual", payload.inputs if payload else None
            )
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        await _delete_run_threads(pruned)
        _track_background(asyncio.create_task(_execute_and_announce(trigger_id, run.run_id)))
        return JSONResponse({"task": trigger.to_dict(), "run": run.to_dict()})

    def _reopen_workflow_run(trigger_id: str, run_id: str) -> tuple[Any, Any] | JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        if trigger is None or trigger.workflow is None:
            return JSONResponse({"error": f"No workflow task {trigger_id!r}"}, status_code=404)
        existing = trigger.find_run(run_id)
        if existing is None:
            return JSONResponse({"error": f"No run {run_id!r}"}, status_code=404)
        try:
            run = store.reopen_run(trigger_id, run_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return existing, run

    def _continue_in_background(trigger_id: str, run_id: str, action: Any) -> None:
        async def go() -> None:
            run = await continue_workflow_run(
                settings.state_dir, trigger_id, run_id, _get_session_async, action
            )
            await _announce_run(trigger_id, run)

        _track_background(asyncio.create_task(go()))

    @app.post("/api/scheduled-tasks/{trigger_id}/runs/{run_id}/stop")
    async def stop_workflow_run(trigger_id: str, run_id: str) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        run = trigger.find_run(run_id) if trigger is not None else None
        if trigger is None or run is None:
            return JSONResponse({"error": f"No run {run_id!r}"}, status_code=404)
        if run.status != "running" or not stop_run(run_id, run.thread_id):
            return JSONResponse({"error": "This run isn't going"}, status_code=409)
        return JSONResponse({"ok": True})

    @app.post("/api/scheduled-tasks/{trigger_id}/runs/{run_id}/investigate")
    async def investigate_workflow_run(
        trigger_id: str, run_id: str, payload: InvestigateRequest | None = None
    ) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        run = trigger.find_run(run_id) if trigger is not None else None
        if trigger is None or trigger.workflow is None or run is None:
            return JSONResponse({"error": f"No workflow run {run_id!r}"}, status_code=404)
        if run.status != "failed":
            return JSONResponse({"error": "Only a failed run can be looked into"}, status_code=409)
        thread_id = uuid.uuid4().hex[:8]
        _track_background(
            asyncio.create_task(
                start_investigation(
                    settings.state_dir,
                    trigger_id,
                    run_id,
                    thread_id,
                    payload.model if payload else None,
                    _get_session_async,
                    attended=True,
                )
            )
        )
        return JSONResponse({"thread_id": thread_id})

    def _waiting_permission(run: Any) -> dict[str, Any] | None:
        """What the run's waiting step asks to be allowed, if it is asking."""
        for record in reversed(run.steps):
            if record.get("status") == "waiting":
                asked = record.get("permission")
                return asked if isinstance(asked, dict) else None
        return None

    async def _allow_for_run(
        store: ScheduledTriggerStore, trigger_id: str, thread_id: str, permission: dict[str, Any]
    ) -> str | None:
        """Record the answer on the task, so later runs don't ask again, and
        open the folder to the run's session. A problem, or None."""
        target = str(permission.get("target") or "")
        if permission.get("kind") == "folder":
            session = await _get_session_async(thread_id)
            if not await session.add_folder(target, cast(Any, _NoSocket())):
                return f"Couldn't add {target} -- is it still there?"
            _write_workspace_sidecar(thread_id, [str(f) for f in session.folders])
            store.grant(trigger_id, "folders", target)
        elif permission.get("kind") == "site":
            store.grant(trigger_id, "sites", target)
        return None

    @app.post("/api/scheduled-tasks/{trigger_id}/runs/{run_id}/answer")
    async def answer_workflow_approval(
        trigger_id: str, run_id: str, payload: WorkflowAnswer
    ) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        before = trigger.find_run(run_id) if trigger is not None else None
        if before is not None and before.status != "needs_approval":
            return JSONResponse({"error": "This run isn't waiting for approval"}, status_code=409)
        permission = _waiting_permission(before) if before is not None else None
        if payload.approved and permission is not None and before is not None:
            problem = await _allow_for_run(store, trigger_id, before.thread_id, permission)
            if problem:
                return JSONResponse({"error": problem}, status_code=409)
        reopened = _reopen_workflow_run(trigger_id, run_id)
        if isinstance(reopened, JSONResponse):
            return reopened
        _continue_in_background(
            trigger_id, run_id, lambda wf: wf.answer(payload.approved, payload.note)
        )
        return JSONResponse({"run": reopened[1].to_dict()})

    @app.post("/api/scheduled-tasks/{trigger_id}/runs/{run_id}/retry")
    async def retry_workflow_run(
        trigger_id: str, run_id: str, payload: WorkflowRetry
    ) -> JSONResponse:
        reopened = _reopen_workflow_run(trigger_id, run_id)
        if isinstance(reopened, JSONResponse):
            return reopened
        before, run = reopened
        trigger = ScheduledTriggerStore(settings.state_dir).load(trigger_id)
        workflow = parse_workflow(trigger.workflow) if trigger and trigger.workflow else None
        # A loop or branch around the failed step is recorded as failed
        # too; retrying means the step itself, on the pass that failed.
        blocks = (
            {p.step.id for p in walk(workflow.steps) if isinstance(p.step, (BranchStep, LoopStep))}
            if workflow
            else set()
        )
        step_id = payload.step_id or next(
            (
                s["step_id"]
                for s in reversed(before.steps)
                if s.get("status") == "failed" and s["step_id"] not in blocks
            ),
            None,
        )
        if step_id is None:
            _continue_in_background(trigger_id, run_id, lambda wf: wf.resume())
        else:
            _continue_in_background(trigger_id, run_id, lambda wf: wf.retry_from(step_id))
        return JSONResponse({"run": run.to_dict()})

    @app.get("/api/scheduled-tasks/{trigger_id}/notes")
    async def get_scheduled_task_notes(trigger_id: str) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        if store.load(trigger_id) is None:
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        return JSONResponse({"notes": store.read_notes(trigger_id)})

    @app.put("/api/scheduled-tasks/{trigger_id}/notes")
    async def put_scheduled_task_notes(trigger_id: str, payload: TaskNotesUpdate) -> JSONResponse:
        store = ScheduledTriggerStore(settings.state_dir)
        if store.load(trigger_id) is None:
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        try:
            store.write_notes(trigger_id, payload.notes.strip())
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"notes": store.read_notes(trigger_id)})

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
        store = ScheduledTriggerStore(settings.state_dir)
        trigger = store.load(trigger_id)
        if trigger is None or not store.delete(trigger_id):
            return JSONResponse({"error": f"No scheduled task {trigger_id!r}"}, status_code=404)
        await _delete_run_threads(trigger.runs)
        return JSONResponse({"deleted": trigger_id})

    @app.get("/api/threads/{thread_id}/environment")
    async def get_session_environment(thread_id: str) -> dict[str, Any]:
        return SessionEnvironments(settings.state_dir).get(thread_id)

    @app.put("/api/threads/{thread_id}/environment")
    async def put_session_environment(thread_id: str, payload: dict[str, Any]) -> Any:
        try:
            return SessionEnvironments(settings.state_dir).set(
                thread_id,
                payload.get("variables", {}),
                payload.get("secrets", []),
                SecretStore(settings.state_dir).names(),
            )
        except SecretError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @app.get("/api/mcp/catalog")
    async def get_mcp_catalog() -> list[dict[str, Any]]:
        """The catalog, each entry's tools being what the service reported
        when it was connected if that ever happened, else what the maker's
        documentation lists."""
        seen = SeenTools(settings.state_dir)
        for entry in MCP_CATALOG:
            name = entry["name"]
            if name in mcp_connections:
                seen.remember(name, [t["name"][len(name) + 1 :] for t in _connector_tools(name)])
        remembered = seen.load()
        result = []
        for entry in MCP_CATALOG:
            tools = remembered.get(entry["name"])
            if tools:
                result.append({**entry, "tools": tools, "tools_from": "service"})
            elif entry.get("tools"):
                result.append({**entry, "tools_from": "docs"})
            else:
                result.append(entry)
        return result

    async def _begin_oauth_sign_in(name: str, config: Any, request: Request) -> dict[str, Any]:
        """Starts signing in to a connector and returns once there is
        something to show: connected, failed, or the sign-in page's address
        (already opened in the user's browser). The connection keeps
        waiting for the redirect in the background; the Connectors page
        sees the result through GET /api/mcp/servers."""
        app_group = config.get("oauth_app")
        setup: dict[str, Any] = next(
            (
                e["setup"]
                for e in MCP_CATALOG
                if app_group and e.get("setup", {}).get("group") == app_group
            ),
            {},
        )
        if app_group:
            if mcp_oauth.app(app_group) is None:
                return {"connected": False, "error": "Set up the app for this connector first."}
            try:
                await app_callback.start()
            except CallbackPortBusy as exc:
                return {"connected": False, "error": str(exc)}
            redirect_uri = APP_REDIRECT_URI
        else:
            redirect_uri = f"{str(request.base_url).rstrip('/')}/api/mcp/oauth/callback"
        try:
            flow = await mcp_oauth.begin(
                name,
                redirect_uri,
                app=app_group,
                scope=" ".join(setup["scopes"]) if setup.get("scopes") else None,
                auth_params=setup.get("auth_params"),
            )
        except NeedsSignIn as exc:
            await app_callback.stop()
            return {"connected": False, "error": str(exc)}
        await _disconnect_mcp_server_lg(name)

        async def connect() -> None:
            connected, error = False, None
            try:
                connected, error = await _connect_and_register_mcp_server_lg(
                    name, config, SIGN_IN_TIMEOUT_SECONDS + 30
                )
                await _refresh_all_sessions_extra_tools()
            except Exception as exc:  # noqa: BLE001 -- shown to the user, not swallowed
                error = str(exc) or type(exc).__name__
            mcp_oauth.finish(flow, None if connected else error or "Couldn't sign in.")
            if app_callback.running and not any(
                mcp_oauth.pending(n) for n in list(mcp_oauth._active)
            ):
                await app_callback.stop()

        task = asyncio.create_task(connect())
        sign_in_tasks.add(task)
        task.add_done_callback(sign_in_tasks.discard)
        try:
            await asyncio.wait_for(flow.url_ready.wait(), timeout=30)
        except TimeoutError:
            return {"connected": False, "error": "The server didn't offer a sign-in page."}
        if flow.finished.is_set():
            return {"connected": flow.error is None, "error": flow.error}
        return {"connected": False, "signin": {"url": flow.url}}

    @app.get("/api/mcp/oauth-apps")
    async def get_mcp_oauth_apps() -> dict[str, Any]:
        return {"saved": mcp_oauth.app_groups(), "redirect_uri": APP_REDIRECT_URI}

    @app.put("/api/mcp/oauth-apps/{group}")
    async def put_mcp_oauth_app(group: str, payload: OAuthAppCredentials) -> JSONResponse:
        client_id = payload.client_id.strip()
        secret = (payload.client_secret or "").strip() or None
        if not client_id or any(ch.isspace() for ch in client_id):
            return JSONResponse(
                {"error": "The Client ID is empty or has spaces in it."}, status_code=400
            )
        needs_secret = any(
            e.get("setup", {}).get("group") == group and e["setup"].get("needs_secret")
            for e in MCP_CATALOG
        )
        if needs_secret and secret is None:
            return JSONResponse(
                {"error": "This service needs the Client secret too."}, status_code=400
            )
        if secret is not None and any(ch.isspace() for ch in secret):
            return JSONResponse({"error": "The Client secret has spaces in it."}, status_code=400)
        mcp_oauth.set_app(group, client_id, secret)
        return JSONResponse({"saved": group})

    @app.delete("/api/mcp/oauth-apps/{group}")
    async def delete_mcp_oauth_app(group: str) -> dict[str, Any]:
        mcp_oauth.forget_app(group)
        return {"removed": group}

    @app.get("/api/mcp/oauth-apps/{group}/export")
    async def export_mcp_oauth_app(group: str) -> JSONResponse:
        credentials = mcp_oauth.app(group)
        if credentials is None:
            return JSONResponse({"error": "Nothing saved for this service."}, status_code=404)
        return JSONResponse(
            {
                "apps": {
                    group: {"client_id": credentials[0], "client_secret": credentials[1]},
                }
            }
        )

    @app.post("/api/mcp/oauth-apps/import")
    async def import_mcp_oauth_apps(payload: OAuthAppImport) -> JSONResponse:
        for group, credentials in payload.apps.items():
            if not credentials.client_id.strip():
                return JSONResponse({"error": f"{group}: the Client ID is empty."}, status_code=400)
        for group, credentials in payload.apps.items():
            mcp_oauth.set_app(group, credentials.client_id.strip(), credentials.client_secret)
        return JSONResponse({"imported": sorted(payload.apps)})

    @app.get("/api/mcp/oauth/callback", response_class=HTMLResponse)
    async def mcp_oauth_callback(
        state: str = "", code: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        status, body = _oauth_callback_response(state, code, error)
        return HTMLResponse(body, status_code=status)

    @app.post("/api/mcp/servers/{name}/signin")
    async def sign_in_mcp_server(name: str, request: Request) -> dict[str, Any]:
        """Signs in again to an OAuth connector whose saved sign-in no longer
        works."""
        if settings.mcp_config_path is None or not settings.mcp_config_path.is_file():
            return {"error": "not found", "connected": False}
        entry = _read_mcp_servers_raw(settings.mcp_config_path)["mcpServers"].get(name)
        if entry is None or entry.get("auth") != "oauth":
            return {"error": "not found", "connected": False}
        config = validate_mcp_config({"type": "mcp", "name": name, **entry})
        return await _begin_oauth_sign_in(name, config, request)

    @app.get("/api/mcp/servers")
    async def get_mcp_servers() -> dict[str, Any]:
        if settings.mcp_config_path is None or not settings.mcp_config_path.is_file():
            return {}
        result: dict[str, Any] = {}
        for name, config in load_mcp_server_configs(
            settings.mcp_config_path, settings.state_dir, fill_secrets=False
        ).items():
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
            try:
                with_secrets(config, settings.state_dir)
                secret_error = None
            except (SecretError, RuntimeError) as exc:
                secret_error = str(exc)
            if "server_url" in config:
                # Remote (streamable_http) entry -- a hand-configured
                # Custom-tab remote-server form. Bearer/auth header values
                # masked the same way env values are below -- never echoed
                # back to the browser in full.
                result[name] = {
                    "server_url": config["server_url"],
                    "masked_headers": {
                        k: _mask_value(v) for k, v in (config.get("headers") or {}).items()
                    },
                    "connected": connected,
                    "secret_error": secret_error,
                    "tools": _connector_tools(name),
                }
                if config.get("auth") == "oauth":
                    waiting = mcp_oauth.pending(name)
                    result[name]["auth"] = "oauth"
                    result[name]["signin"] = (
                        {"url": waiting.url} if waiting is not None and waiting.url else None
                    )
                    result[name]["signin_error"] = mcp_oauth.last_error(name)
            else:
                result[name] = {
                    "command": config.get("command"),
                    "args": config.get("args", []),
                    "masked_env": {k: _mask_value(v) for k, v in (config.get("env") or {}).items()},
                    "connected": connected,
                    "secret_error": secret_error,
                    "tools": _connector_tools(name),
                }
        return result

    @app.post("/api/mcp/servers")
    async def add_mcp_server(payload: MCPServerUpdate, request: Request) -> dict[str, Any]:
        if payload.server_url:
            entry: dict[str, Any] = {"server_url": payload.server_url}
            if payload.headers:
                entry["headers"] = payload.headers
            if payload.auth:
                entry["auth"] = payload.auth
                if payload.oauth_app:
                    entry["oauth_app"] = payload.oauth_app
        else:
            entry = {"command": payload.command, "args": payload.args}
            if payload.env:
                entry["env"] = payload.env
        try:
            config = validate_mcp_config({"type": "mcp", "name": payload.name, **entry})
        except ValueError as exc:
            return {"rejected": {payload.name: str(exc)}, "connected": False}

        try:
            with_secrets(config, settings.state_dir)
        except (SecretError, RuntimeError) as exc:
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

        if config.get("auth") == "oauth":
            return {"rejected": {}, **await _begin_oauth_sign_in(payload.name, config, request)}
        await _disconnect_mcp_server_lg(payload.name)
        connected, error = await _connect_and_register_mcp_server_lg(payload.name, config)
        await _refresh_all_sessions_extra_tools()
        return {"rejected": {}, "connected": connected, "error": error}

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
        ConnectorPermissions(settings.state_dir).forget(name)
        mcp_oauth.forget(name)
        await _refresh_all_sessions_extra_tools()
        return {}

    @app.put("/api/mcp/servers/{name}/permissions")
    async def set_connector_permissions(
        name: str, payload: ConnectorPermissionsUpdate
    ) -> JSONResponse:
        known = {t["name"] for t in _connector_tools(name)}
        unknown = sorted(set(payload.tools) - known)
        if unknown:
            return JSONResponse(
                {"error": f"{name!r} has no tool {', '.join(unknown)}"}, status_code=404
            )
        try:
            tools = ConnectorPermissions(settings.state_dir).update(name, payload.tools)
        except ValueError:
            return JSONResponse(
                {"error": f"a policy must be one of {', '.join(POLICIES)}"}, status_code=422
            )
        await _refresh_all_sessions_extra_tools()
        return JSONResponse({"tools": tools})

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

    @app.post("/api/mcp/servers/{name}/reconnect")
    async def reconnect_mcp_server(name: str) -> dict[str, Any]:
        """Retries a connect for an already-added server, unchanged --
        the gap add_mcp_server's own docstring names: once `isAdded` is
        true there was no way to replay a failed connect short of Remove
        + re-add, which also throws away the saved config. Same
        disconnect-then-reconnect shape as bump_mcp_server_version, just
        without touching the config at all first."""
        if settings.mcp_config_path is None or not settings.mcp_config_path.is_file():
            return {"error": "not found", "connected": False}
        raw = _read_mcp_servers_raw(settings.mcp_config_path)
        entry = raw["mcpServers"].get(name)
        if entry is None:
            return {"error": "not found", "connected": False}
        try:
            config = validate_mcp_config({"type": "mcp", "name": name, **entry})
        except ValueError as exc:
            return {"error": str(exc), "connected": False}
        await _disconnect_mcp_server_lg(name)
        connected, error = await _connect_and_register_mcp_server_lg(name, config)
        await _refresh_all_sessions_extra_tools()
        return {"connected": connected, "error": error}

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
        connected, error = await _connect_and_register_mcp_server_lg(name, config)
        await _refresh_all_sessions_extra_tools()
        return {"connected": connected, "error": error}

    def _configured_models() -> list[str]:
        return [
            f"{name}:{info['default_model']}"
            for name, info in _providers_info().items()
            if info["default_model"]
        ]

    def _providers_info() -> dict[str, Any]:
        env_values = dotenv_values(".env")
        # Fallback for an existing .env from before /api/setup started also
        # writing the per-provider default (see setup() above) -- without
        # this, a provider whose own COSCRIBE_<X>_DEFAULT_MODEL was never
        # set (true for whichever provider first-run setup picked, on any
        # .env written before that fix) silently vanishes from this list,
        # and therefore from the model switcher, the moment a *different*
        # provider becomes the active model and this one is no longer
        # rendered via the switcher's own currentModel-prop fallback.
        default_provider_key, _, default_model_fallback = (
            env_values.get("COSCRIBE_DEFAULT_MODEL") or ""
        ).partition(":")
        result: dict[str, Any] = {}
        for provider in BUILTIN_PROVIDERS:
            api_key = env_resolve_secret_for_display(env_values.get(provider["api_key_env"])) or ""
            if not api_key:
                continue
            default_model = env_values.get(provider["default_model_env"]) or ""
            if not default_model and provider["key"] == default_provider_key:
                default_model = default_model_fallback
            result[provider["key"]] = {
                "base_url": None,
                "default_model": default_model,
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
        workspace: str | None = None,
    ) -> None:
        await websocket.accept()
        session = _get_session(thread_id, workspace)
        # See ChatSessionLG.notify_resync's own docstring -- this is the
        # one place that knows "a real browser tab is watching this
        # thread right now," so it's the one place that sets/clears it.
        # A *new* connection to an already-open thread (two tabs, or a
        # reload racing its own old socket's teardown) simply overwrites
        # this with the newest connection -- acceptable: resync is a
        # best-effort nudge, not a correctness-critical channel, and the
        # newest tab is the one actually worth nudging.
        session._live_websocket = websocket
        meta_store.mark_seen(thread_id)
        try:
            await session.send_state(websocket, on_connect=True)
            await session.send_history(websocket)
            # A pending approval from before a restart or dropped connection
            # doesn't wait for a new user_message to surface -- redeliver it
            # now. Backgrounded (not awaited) for the same reason
            # user_message handling is: it can block on a future that only
            # resolves via an approval_response arriving through the loop
            # below, so awaiting it inline here would deadlock.
            # Tracked, so shutdown stops it before the checkpointer it reads
            # closes.
            _track_background(asyncio.create_task(session.resume_after_reconnect(websocket)))
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
                elif message_type == "rewind_message":
                    # Never blocks on an approval future the way edit's
                    # rerun can, but create_task anyway -- consistent with
                    # every other mutating message type here, and safe
                    # regardless since it only ever awaits _turn_lock.
                    asyncio.create_task(session.handle_rewind_message(data["index"], websocket))
                elif message_type == "approval_response":
                    session.resolve_approval(
                        data["id"], bool(data.get("approved")), data.get("scope")
                    )
                elif message_type == "question_response":
                    answers = data.get("answers")
                    if data.get("dismissed"):
                        session.resolve_question(data["id"], None)
                    elif isinstance(answers, list):
                        session.resolve_question(
                            data["id"], [None if a is None else str(a) for a in answers]
                        )
                    else:
                        session.resolve_question(data["id"], str(data.get("answer", "")))
                elif message_type == "steer":
                    session.add_steer(str(data.get("id", "")), str(data.get("text", "")))
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
                elif message_type == "set_folders":
                    folders = [str(folder) for folder in data.get("folders", [])]
                    # Persisted only once the session took them, so the
                    # sidecar never names folders the agent isn't using.
                    if await session.set_folders(folders, websocket):
                        _write_workspace_sidecar(thread_id, [str(f) for f in session.folders])
                elif message_type == "add_folder":
                    if await session.add_folder(str(data.get("folder", "")), websocket):
                        _write_workspace_sidecar(thread_id, [str(f) for f in session.folders])
                elif message_type == "load_older_messages":
                    # Directly awaited, not asyncio.create_task like
                    # user_message -- same reasoning as switch_model above:
                    # this never blocks on a human, so there's nothing to
                    # keep this loop free to service concurrently.
                    await session.load_older_messages(websocket)
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
            session.abandon_orphaned_turn(websocket)
            await session.run_session_end_hooks()
        finally:
            # Only clear if this is still *this* connection's own socket --
            # a newer connection (see the comment above where this is set)
            # may have already overwritten it with itself, and this older,
            # now-dead connection's teardown must not clobber that.
            if session._live_websocket is websocket:
                session._live_websocket = None

    state = AppState(
        settings=settings,
        context_window_client=context_window_client,
        get_session=_get_session,
        session_extra_tools=_session_extra_tools,
        skills_by_name=_skills_by_name,
        providers_info=_providers_info,
    )
    for build_router in (settings_router, files_router, skills_router):
        app.include_router(build_router(state))

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
    parser = argparse.ArgumentParser(description="Run coscribe's local web UI.")
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

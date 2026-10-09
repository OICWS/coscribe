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
import html
import json
import logging
import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import uvicorn
from dotenv import dotenv_values, load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .. import __version__
from ..cli import _dotenv_path, _load_settings_or_none
from ..code_runtime.service import (
    remove_stale_code_sidecars,
    shutdown_code_services,
)
from ..config import Settings
from ..conversation.session import ChatSessionLG
from ..conversation.thread_meta import ThreadMetaStore
from ..runtime import (
    LLMClient,
    empty_hooks_config,
    env_resolve_secret_for_display,
    load_custom_providers,
    load_hooks_config,
    resolve_env_keyring_refs,
    resolve_secret,
    run_hook,
)
from ..runtime.secret_store import (
    SecretError,
    SessionEnvironments,
)
from ..runtime.types import get_tool_metadata
from ..runtime_lg import (
    execute_run,
    poll_due_scheduled_tasks,
    poll_due_wakes,
    reconcile_interrupted_runs,
    start_investigation,
)
from ..tools import (
    SkillInfo,
    load_builtin_skills,
    load_skills,
)
from ..tools.connector_permissions import (
    ConnectorPermissions,
    apply_connector_permissions,
    connector_of,
    is_read_only,
    name_of,
)
from ..tools.mcp import (
    prepare_for_connect,
    validate_mcp_config,
)
from ..tools.scheduled_tasks import (
    SCHEDULED_THREAD_PREFIX,
    ScheduledRun,
    ScheduledTriggerStore,
    parse_run_thread_id,
)
from ..tools.skill_catalog import (
    enabled_skill_names,
)
from ..workflows.catalog import tool_description
from ..workflows.permissions import granted_by
from ..workflows.spec import parse_workflow
from .background_events import BackgroundEvent, BackgroundEventBus, run_event
from .provider_catalog import (
    BUILTIN_PROVIDERS,
)
from .routes.connectors import router as connectors_router
from .routes.files import router as files_router
from .routes.internal import router as internal_router
from .routes.scheduled import router as scheduled_router
from .routes.settings import router as settings_router
from .routes.shared import _mask, _read_mcp_servers_raw, _read_providers_raw
from .routes.skills import router as skills_router
from .routes.threads import router as threads_router
from .routes.ws import router as ws_router
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
        '<body style="font-family:system-ui,sans-serif;max-width:32rem;margin:20vh auto;'
        f'padding:0 1rem"><h2>coscribe</h2><p>{html.escape(message)}</p></body>'
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
        LoopbackCallback,
        McpOAuth,
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

    state = AppState(
        settings=settings,
        context_window_client=context_window_client,
        meta_store=meta_store,
        sessions=sessions,
        checkpointer_holder=checkpointer_holder,
        background_events=background_events,
        mcp_connections=mcp_connections,
        mcp_oauth=mcp_oauth,
        app_callback=app_callback,
        sign_in_tasks=sign_in_tasks,
        get_session=_get_session,
        get_session_async=_get_session_async,
        session_extra_tools=_session_extra_tools,
        skills_by_name=_skills_by_name,
        providers_info=_providers_info,
        connector_tools=_connector_tools,
        connect_and_register_mcp_server_lg=_connect_and_register_mcp_server_lg,
        disconnect_mcp_server_lg=_disconnect_mcp_server_lg,
        refresh_all_sessions_extra_tools=_refresh_all_sessions_extra_tools,
        oauth_callback_response=_oauth_callback_response,
        read_workspace_sidecar=_read_workspace_sidecar,
        write_workspace_sidecar=_write_workspace_sidecar,
        title_sidecar_path=_title_sidecar_path,
        delete_thread_data=_delete_thread_data,
        delete_run_threads=_delete_run_threads,
        execute_and_announce=_execute_and_announce,
        announce_run=_announce_run,
        track_background=_track_background,
    )
    for build_router in (
        internal_router,
        threads_router,
        scheduled_router,
        settings_router,
        skills_router,
        files_router,
        connectors_router,
        ws_router,
    ):
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

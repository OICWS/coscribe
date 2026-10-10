"""Settings routes."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, get_args

from dotenv import dotenv_values, set_key
from fastapi import (
    APIRouter,
)
from fastapi.responses import JSONResponse

from ...code_runtime.install import CodexUnavailable
from ...code_runtime.launch import codex_model
from ...code_runtime.permissions import CodePermissions
from ...code_runtime.service import (
    code_service,
)
from ...config import PermissionMode
from ...coordinator import build_coordinator_agent
from ...runtime import (
    delete_secret,
    env_delete_secret_if_ref,
    env_resolve_secret_for_display,
    env_value_for_storage,
    harden_file_permissions,
    resolve_secret,
    store_secret,
)
from ...runtime.secret_store import (
    KeychainUnavailable,
    SecretError,
    SecretStore,
    keychain_available,
)
from ...runtime.types import get_tool_metadata
from ...runtime_lg.code_agent import CODE_APPROVAL_RISKS
from ...tools import (
    load_builtin_skills,
    load_skills,
)
from ...tools.mcp import (
    secret_names_used,
)
from ...tools.memory import load_memory
from ...tools.node_env import install_package as install_node_package
from ...tools.node_env import list_packages as list_node_packages
from ...tools.node_env import uninstall_package as uninstall_node_package
from ...tools.script_env import (
    fallbacks_for_platform,
    get_interpreter_override,
    install_package,
    list_packages,
    set_interpreter_override,
    uninstall_package,
    working_interpreters,
)
from ...workflows.catalog import describe_params, tool_description
from ..provider_catalog import (
    BUILTIN_PROVIDERS,
    PROVIDER_CATALOG,
    PROVIDER_DEFAULT_MODEL_ENV_VARS,
    PROVIDER_KEY_ENV_VARS,
)
from ..schemas import (
    ConfigUpdate,
    MemoryUpdate,
    ProviderUpdate,
    ScriptEnvInterpreterUpdate,
    ScriptEnvPackageInstall,
)
from ..state import AppState
from .shared import _mask, _read_mcp_servers_raw, _read_providers_raw

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
    "COSCRIBE_DEFAULT_PERMISSION_MODE",
    "COSCRIBE_CODE_MODEL",
    "COSCRIBE_CODE_MODULE_ENABLED",
]


# Settings update_config applies to the running server as well as .env.
LIVE_SETTINGS = {
    "COSCRIBE_DEFAULT_MODEL": "default_model",
    "COSCRIBE_MAX_TURNS": "max_turns",
    "COSCRIBE_DEFAULT_PERMISSION_MODE": "default_permission_mode",
    "COSCRIBE_CODE_MODEL": "code_model",
    "COSCRIBE_CODE_MODULE_ENABLED": "code_module_enabled",
}


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
DESKTOP_ENV_VARS = ["COSCRIBE_BACKGROUND_ON_CLOSE", "COSCRIBE_NOTIFICATIONS"]


# Kept in .env rather than the browser's own storage: the desktop app
# serves the page from a new port each launch, and browser storage
# doesn't survive an origin change.
APPEARANCE_ENV_VARS: dict[str, tuple[str, ...]] = {
    "COSCRIBE_THEME": ("system", "light", "dark"),
    "COSCRIBE_INTERFACE_FONT": ("default", "system", "dyslexic"),
    "COSCRIBE_MOTION": ("system", "reduced"),
}


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
    "COSCRIBE_DEFAULT_PERMISSION_MODE",
}


_BOOLEAN_CODE_KEYS = {"COSCRIBE_CODE_MODULE_ENABLED"}


FIXED_COMMANDS = [
    {"name": "plan", "description": "Toggle Plan Mode (read-only tools only)"},
    {"name": "accept-edits", "description": "Toggle Accept-Edits Mode (no approval prompts)"},
    {"name": "compact", "description": "Summarize this thread to reclaim context"},
    {"name": "clear", "description": "Wipe this thread's conversation history and start fresh"},
    {"name": "stop", "description": "Stop the current in-progress run"},
    {"name": "init", "description": "Explore the workspace and write OVERVIEW.md"},
    {
        "name": "saveworkflow",
        "description": "Draft a workflow from what this conversation did "
        "(usage: /saveworkflow [name])",
    },
    {
        "name": "saveskill",
        "description": "Save this conversation as a reusable Skill (usage: /saveskill <name>)",
    },
]


def router(state: AppState) -> APIRouter:
    router = APIRouter()
    settings = state.settings
    _session_extra_tools = state.session_extra_tools
    _providers_info = state.providers_info
    context_window_client = state.context_window_client

    @router.get("/api/commands")
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

    @router.get("/api/tools")
    async def get_tools() -> dict[str, Any]:
        agent = build_coordinator_agent(settings, thread_id="__tools_probe__")
        tools = []
        # Connected connectors' tools can be workflow steps too.
        for connector_tool in _session_extra_tools():
            metadata = get_tool_metadata(connector_tool)
            if not (metadata.category or "").startswith("mcp:"):
                continue
            tools.append(
                {
                    "name": connector_tool.name,
                    "category": metadata.category,
                    "risk_category": metadata.risk_category,
                    "requires_approval": metadata.requires_approval,
                    "description": tool_description(connector_tool).split(". ")[0],
                    "params": describe_params(connector_tool),
                }
            )
        for tool in agent.tools:
            metadata = get_tool_metadata(tool)
            doc = inspect.getdoc(tool) or ""
            # The docstring's first sentence, which often wraps past its
            # first line.
            first_paragraph = " ".join(doc.split("\n\n", 1)[0].split())
            description = first_paragraph.split(". ", 1)[0].rstrip(".") + "." if doc else ""
            tools.append(
                {
                    "name": tool.__name__,
                    "category": metadata.category or "",
                    "risk_category": metadata.risk_category,
                    "requires_approval": metadata.requires_approval,
                    "description": description,
                    "params": describe_params(tool),
                }
            )
        return {"tools": tools}

    # -- /api/config, /api/mcp/*, /api/providers/* -- direct ports of
    # web/app.py's identical endpoints (see this module's docstring for the
    # one behavioral difference: config changes here apply to the next new
    # session, not every already-open one).

    @router.get("/api/config")
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
        for key in APPEARANCE_ENV_VARS:
            result[key] = values.get(key) or None
        return result

    @router.post("/api/config")
    async def update_config(payload: ConfigUpdate) -> dict[str, Any]:
        allowed = (
            set(PROVIDER_KEY_ENV_VARS)
            | set(PROVIDER_DEFAULT_MODEL_ENV_VARS)
            | set(COSCRIBE_ENV_VARS)
            | set(DESKTOP_ENV_VARS)
            | set(APPEARANCE_ENV_VARS)
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
            if key == "COSCRIBE_CODE_MODEL" and value.strip() and ":" not in value:
                rejected[key] = 'must be a "provider:model" string, or blank for the default model'
                continue
            if key in _BOOLEAN_CODE_KEYS and value not in ("true", "false"):
                rejected[key] = 'must be "true" or "false"'
                continue
            if key == "COSCRIBE_MAX_TURNS" and not (value.strip().isdigit() and int(value) > 0):
                rejected[key] = "must be a positive integer"
                continue
            if key == "COSCRIBE_DEFAULT_PERMISSION_MODE" and value not in get_args(PermissionMode):
                rejected[key] = f"must be one of {', '.join(get_args(PermissionMode))}"
                continue
            if key in APPEARANCE_ENV_VARS and value not in APPEARANCE_ENV_VARS[key]:
                rejected[key] = f"must be one of {', '.join(APPEARANCE_ENV_VARS[key])}"
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
                if key in LIVE_SETTINGS:
                    # Read afresh whenever a session starts, so the running
                    # server can take the new value without a restart.
                    live_value: str | int | bool | None = value
                    if key == "COSCRIBE_MAX_TURNS":
                        live_value = int(value)
                    elif key in _BOOLEAN_CODE_KEYS:
                        live_value = value == "true"
                    elif key == "COSCRIBE_CODE_MODEL":
                        live_value = value.strip() or None
                    setattr(settings, LIVE_SETTINGS[key], live_value)
            applied.add(key)
        restart_required = any(
            key in COSCRIBE_ENV_VARS and key not in LIVE_SETTINGS for key in applied
        )
        return {"restart_required": restart_required, "rejected": rejected}

    def _code_permissions() -> CodePermissions:
        return CodePermissions(settings.state_dir, tuple(CODE_APPROVAL_RISKS))

    @router.get("/api/code")
    async def code_status() -> dict[str, Any]:
        """The code module's download, and whether Codex can use the model
        a chat's code task would run on (the chat's own, unless one is set)."""
        service = code_service(settings)
        model = settings.code_model or settings.default_model
        try:
            codex_model(model, service.custom_providers())
            model_problem = None
        except CodexUnavailable as exc:
            model_problem = str(exc)
        return {**service.status(), "model": model, "model_problem": model_problem}

    # -- Secrets: values are write-only; nothing here ever returns one.

    def _connectors_using_secret(name: str) -> list[str]:
        if settings.mcp_config_path is None or not settings.mcp_config_path.is_file():
            return []
        servers = _read_mcp_servers_raw(settings.mcp_config_path)["mcpServers"]
        return sorted(
            server for server, entry in servers.items() if name in _secret_names_in_entry(entry)
        )

    def _secret_names_in_entry(entry: dict[str, Any]) -> set[str]:
        # Stored values may be keychain references, so read them back first.
        resolved: dict[str, Any] = {}
        for field in ("env", "headers"):
            values = entry.get(field) or {}
            resolved[field] = {k: _stored_text(v) for k, v in values.items()}
        return secret_names_used(resolved)

    def _stored_text(value: Any) -> str:
        # An unreadable value could be the one that names the secret, so the
        # caller treats it as in use rather than as nothing.
        return resolve_secret(value) or ""

    @router.get("/api/secrets")
    async def list_secrets() -> dict[str, Any]:
        return {
            "keychain": keychain_available(),
            "secrets": SecretStore(settings.state_dir).entries(),
        }

    @router.put("/api/secrets/{name}")
    async def put_secret(name: str, payload: dict[str, Any]) -> Any:
        try:
            return SecretStore(settings.state_dir).save(
                name, payload.get("value"), payload.get("hosts")
            )
        except KeychainUnavailable as exc:
            return JSONResponse(
                {"error": str(exc), "code": "keychain_unavailable"}, status_code=503
            )
        except SecretError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @router.delete("/api/secrets/{name}")
    async def delete_secret_endpoint(name: str) -> Any:
        try:
            using = _connectors_using_secret(name)
        except RuntimeError as exc:
            return JSONResponse(
                {
                    "error": "A connector's saved settings can't be read from the keychain, so "
                    f"it can't be checked whether {name} is in use: {exc}"
                },
                status_code=503,
            )
        if using:
            return JSONResponse(
                {
                    "error": f"{name} is used by the connector {', '.join(using)}. "
                    "Change that connector first.",
                    "connectors": using,
                },
                status_code=409,
            )
        if not SecretStore(settings.state_dir).delete(name):
            return JSONResponse({"error": f"There is no secret named {name}."}, status_code=404)
        return {"deleted": name}

    @router.get("/api/code/permissions")
    async def get_code_permissions() -> dict[str, Any]:
        return _code_permissions().load()

    @router.put("/api/code/permissions")
    async def put_code_permissions(payload: dict[str, str]) -> Any:
        try:
            return _code_permissions().update(payload)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=422)

    @router.post("/api/code/install")
    async def install_code() -> Any:
        try:
            await code_service(settings).prepare()
        except Exception as exc:  # noqa: BLE001 -- a failed download is reported to the page
            return JSONResponse({"error": str(exc) or type(exc).__name__}, status_code=502)
        return await code_status()

    @router.delete("/api/code/install")
    async def remove_code() -> Any:
        if not await code_service(settings).remove():
            return JSONResponse(
                {"error": "The code module is working. Try again once it's done."},
                status_code=409,
            )
        return await code_status()

    @router.get("/api/memory")
    async def get_memory() -> dict[str, Any]:
        # Settings.memory_path can change mid-session (a folder change
        # re-resolves it, see set_folders) -- reading settings.memory_path
        # fresh here rather than caching it at app-build time keeps this
        # endpoint honest about whichever file the *next* new thread would
        # actually load, same "read fresh, no stale cache" posture
        # get_config's own dotenv_values(".env") call takes.
        return {"content": load_memory(settings.memory_path)}

    @router.post("/api/memory")
    async def update_memory(payload: MemoryUpdate) -> dict[str, Any]:
        # Directly overwrites the file -- the Settings panel's own text
        # box is the whole editing surface here, so a full
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

    @router.get("/api/providers/catalog")
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

    @router.get("/api/providers")
    async def get_providers() -> dict[str, Any]:
        return _providers_info()

    @router.post("/api/providers")
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

    @router.delete("/api/providers/{name}")
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
    @router.get("/api/script-env/packages")
    async def get_script_env_packages() -> list[dict[str, str]]:
        return await asyncio.to_thread(list_packages, settings.state_dir)

    @router.post("/api/script-env/packages")
    async def add_script_env_package(payload: ScriptEnvPackageInstall) -> dict[str, object]:
        name = payload.package.strip()
        if not name:
            return {"success": False, "error": "Package name cannot be blank."}
        return await asyncio.to_thread(install_package, settings.state_dir, name)

    @router.delete("/api/script-env/packages/{name}")
    async def remove_script_env_package(name: str) -> dict[str, object]:
        return await asyncio.to_thread(uninstall_package, settings.state_dir, name)

    @router.get("/api/script-env/interpreter")
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

    @router.post("/api/script-env/interpreter")
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
    @router.get("/api/node-env/packages")
    async def get_node_env_packages() -> list[dict[str, str]]:
        return await asyncio.to_thread(list_node_packages, settings.state_dir)

    @router.post("/api/node-env/packages")
    async def add_node_env_package(payload: ScriptEnvPackageInstall) -> dict[str, object]:
        name = payload.package.strip()
        if not name:
            return {"success": False, "error": "Package name cannot be blank."}
        return await asyncio.to_thread(install_node_package, settings.state_dir, name)

    @router.delete("/api/node-env/packages/{name}")
    async def remove_node_env_package(name: str) -> dict[str, object]:
        return await asyncio.to_thread(uninstall_node_package, settings.state_dir, name)

    return router

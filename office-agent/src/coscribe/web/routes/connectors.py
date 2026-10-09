"""Connectors routes."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from dotenv import set_key
from fastapi import (
    APIRouter,
    Request,
)
from fastapi.responses import HTMLResponse, JSONResponse

from ...runtime import (
    delete_secret,
    harden_file_permissions,
    store_secret,
)
from ...runtime.secret_store import (
    SecretError,
    placeholders_in,
)

# Deferred import for the same reason as runtime_lg.mcp (see the comment
# above MCP_STARTUP_TIMEOUT_SECONDS).
from ...runtime_lg.mcp_oauth import (
    APP_REDIRECT_URI,
    SIGN_IN_TIMEOUT_SECONDS,
    CallbackPortBusy,
    NeedsSignIn,
)
from ...tools.connector_permissions import (
    POLICIES,
    ConnectorPermissions,
)
from ...tools.mcp import (
    load_mcp_server_configs,
    validate_mcp_config,
    with_secrets,
)
from ..connector_catalog import MCP_CATALOG, SeenTools
from ..schemas import (
    ConnectorPermissionsUpdate,
    MCPServerUpdate,
    MCPVersionBump,
    OAuthAppCredentials,
    OAuthAppImport,
)
from ..state import AppState
from .shared import _mask, _read_mcp_servers_raw


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


def router(state: AppState) -> APIRouter:
    router = APIRouter()
    _connector_tools = state.connector_tools
    mcp_connections = state.mcp_connections
    settings = state.settings
    _connect_and_register_mcp_server_lg = state.connect_and_register_mcp_server_lg
    _disconnect_mcp_server_lg = state.disconnect_mcp_server_lg
    _refresh_all_sessions_extra_tools = state.refresh_all_sessions_extra_tools
    app_callback = state.app_callback
    mcp_oauth = state.mcp_oauth
    sign_in_tasks = state.sign_in_tasks
    _oauth_callback_response = state.oauth_callback_response


    @router.get("/api/mcp/catalog")
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

    @router.get("/api/mcp/oauth-apps")
    async def get_mcp_oauth_apps() -> dict[str, Any]:
        return {"saved": mcp_oauth.app_groups(), "redirect_uri": APP_REDIRECT_URI}

    @router.put("/api/mcp/oauth-apps/{group}")
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

    @router.delete("/api/mcp/oauth-apps/{group}")
    async def delete_mcp_oauth_app(group: str) -> dict[str, Any]:
        mcp_oauth.forget_app(group)
        return {"removed": group}

    @router.get("/api/mcp/oauth-apps/{group}/export")
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

    @router.post("/api/mcp/oauth-apps/import")
    async def import_mcp_oauth_apps(payload: OAuthAppImport) -> JSONResponse:
        for group, credentials in payload.apps.items():
            if not credentials.client_id.strip():
                return JSONResponse({"error": f"{group}: the Client ID is empty."}, status_code=400)
        for group, credentials in payload.apps.items():
            mcp_oauth.set_app(group, credentials.client_id.strip(), credentials.client_secret)
        return JSONResponse({"imported": sorted(payload.apps)})

    @router.get("/api/mcp/oauth/callback", response_class=HTMLResponse)
    async def mcp_oauth_callback(
        state: str = "", code: str | None = None, error: str | None = None
    ) -> HTMLResponse:
        status, body = _oauth_callback_response(state, code, error)
        return HTMLResponse(body, status_code=status)

    @router.post("/api/mcp/servers/{name}/signin")
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

    @router.get("/api/mcp/servers")
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

    @router.post("/api/mcp/servers")
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

    @router.delete("/api/mcp/servers/{name}")
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

    @router.put("/api/mcp/servers/{name}/permissions")
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

    @router.get("/api/mcp/npm-latest-version")
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

    @router.post("/api/mcp/servers/{name}/reconnect")
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

    @router.post("/api/mcp/servers/{name}/bump-version")
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

    return router

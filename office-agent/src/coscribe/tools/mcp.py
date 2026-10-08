"""MCP (Model Context Protocol) server config parsing.

Loads a Claude-Desktop-style `mcpServers` JSON config and validates each
entry. Connecting to the servers themselves happens in runtime_lg/mcp.py,
via langchain-mcp-adapters -- not aisuite's own MCPClient, whose
MCPToolWrapper collapses each tool's real JSON Schema into a lossy generic
Python signature (see runtime_lg/README.md's "MCP tools under runtime_lg"
section for the live-reproduced bug this caused). This module used to
also hold that aisuite-based connector, deleted once runtime_lg's own
connector fully replaced it -- see runtime_lg/README.md's "audit + delete
old runtime" section.

`validate_mcp_config`/`MCPConfig` below used to be
`aisuite.mcp.config.validate_mcp_config`, reused purely as a validator
(never tied to aisuite's own MCP client). Real, measured startup-time
finding: profiling `coscribe-web-lg`'s known-slow startup
(`python -X importtime`) found that one `from aisuite.mcp.config import
...` here cost ~485ms on its own -- not because that submodule itself is
heavy (it's pure `typing`, zero third-party deps, confirmed by reading
it), but because importing *any* submodule of a package always runs that
package's own `__init__.py` first, and aisuite's pulls in its full
`Client`/`ProviderFactory`/`MCPClient` machinery (which itself imports the
entire `mcp` SDK a second time -- langchain-mcp-adapters, used by
runtime_lg/mcp.py's real connector, already needs `mcp` too, so this was
pure duplicate weight). None of that machinery was ever used here -- this
module only ever wanted the validator function. Vendored a minimal
equivalent below instead of depending on aisuite for it, dropping the
~485ms. Deliberately narrower than aisuite's own version: aisuite's schema
also validates/normalizes `allowed_tools`/`use_tool_prefix`/
`timeout_seconds`/`response_bytes_cap`/`lazy_connect` -- confirmed via
grep that nothing downstream of `load_mcp_server_configs` (runtime_lg/
mcp.py's `_to_lg_connection`, web/app.py's MCP endpoints) ever reads any
of those fields, so validating them was already dead weight even before
this change; only the fields actually used to establish a connection
(`command`/`args`/`env`/`cwd`/`server_url`/`headers`) are validated here.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TypedDict, cast
from urllib.parse import urlsplit

from ..runtime.secret_store import (
    SecretError,
    SecretStore,
    host_allowed,
    placeholders_in,
    substitute,
)
from ..runtime.secrets import resolve_secret

logger = logging.getLogger(__name__)


class MCPConfig(TypedDict, total=False):
    """The subset of aisuite's MCPConfig shape runtime_lg/mcp.py's
    `_to_lg_connection` actually reads -- see this module's docstring for
    why the fuller aisuite schema isn't reproduced here."""

    type: str
    name: str
    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str
    server_url: str
    headers: dict[str, str]
    auth: str
    oauth_app: str


def validate_mcp_config(config: dict[str, Any]) -> MCPConfig:
    """Validate one `mcpServers` entry (already tagged with `type`/`name`
    by `load_mcp_server_configs` below) and return it as a normalized
    `MCPConfig`. Raises `ValueError` with a message naming the actual
    problem, same contract aisuite's own `validate_mcp_config` had."""
    if config.get("type") != "mcp":
        raise ValueError(f"Invalid config type: {config.get('type')}. Expected 'mcp'")

    name = config.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"MCP 'name' must be a non-empty string, got: {name!r}")

    has_stdio = "command" in config
    has_http = "server_url" in config
    if not (has_stdio ^ has_http):
        raise ValueError(
            "MCP config must have either 'command' or 'server_url'. "
            "Use one or the other to specify transport type."
        )

    normalized: MCPConfig = {"type": "mcp", "name": name}
    if has_stdio:
        command = config["command"]
        if not isinstance(command, str):
            raise ValueError(f"MCP 'command' must be a string, got: {type(command)}")
        args = config.get("args", [])
        if not isinstance(args, list):
            raise ValueError(f"MCP 'args' must be a list, got: {type(args)}")
        normalized["command"] = command
        normalized["args"] = args
        if "env" in config:
            env = config["env"]
            if not isinstance(env, dict):
                raise ValueError(f"MCP 'env' must be a dict, got: {type(env)}")
            normalized["env"] = env
        if "cwd" in config:
            normalized["cwd"] = config["cwd"]
    else:
        server_url = config["server_url"]
        if not isinstance(server_url, str):
            raise ValueError(f"MCP 'server_url' must be a string, got: {type(server_url)}")
        if not (server_url.startswith("http://") or server_url.startswith("https://")):
            raise ValueError(
                f"MCP 'server_url' must start with http:// or https://, got: {server_url}"
            )
        normalized["server_url"] = server_url
        if "headers" in config:
            headers = config["headers"]
            if not isinstance(headers, dict):
                raise ValueError(f"MCP 'headers' must be a dict, got: {type(headers)}")
            normalized["headers"] = headers
        if "auth" in config:
            if config["auth"] != "oauth":
                raise ValueError(f"MCP 'auth' must be 'oauth' if given, got: {config['auth']!r}")
            if "headers" in config:
                raise ValueError("MCP 'auth' and 'headers' can't be used together")
            normalized["auth"] = "oauth"
            if "oauth_app" in config:
                app = config["oauth_app"]
                if not isinstance(app, str) or not app.strip():
                    raise ValueError(f"MCP 'oauth_app' must be a non-empty string, got: {app!r}")
                normalized["oauth_app"] = app

    return normalized


def with_secrets(config: MCPConfig, state_dir: Path | None) -> MCPConfig:
    """`config` with each `{{secret:NAME}}` in its env and header values
    replaced by that secret's value from Settings > Secrets, so the value
    lives only in the keychain and never in mcp.json. A header goes only to
    this connector's own server, so the secret must be one allowed for that
    host; an env value goes to the local command the user chose, which has
    no host to check. Raises SecretError, naming the connector and secret."""
    resolved: dict[str, Any] = dict(config)
    host = urlsplit(config["server_url"]).hostname if "server_url" in config else None
    for field in ("env", "headers"):
        values: dict[str, str] | None = config.get(field)  # type: ignore[assignment]
        if not values:
            continue
        resolved[field] = {
            key: _substitute_secrets(
                config["name"], field, value, host if field == "headers" else None, state_dir
            )
            for key, value in values.items()
        }
    return cast(MCPConfig, resolved)


def prepare_for_connect(config: MCPConfig, state_dir: Path | None) -> MCPConfig:
    """`config` as the connection needs it: keychain references read back and
    secret placeholders filled in. Raises SecretError or RuntimeError."""
    prepared: dict[str, Any] = dict(config)
    for field in ("env", "headers"):
        values: dict[str, Any] | None = config.get(field)  # type: ignore[assignment]
        if values:
            prepared[field] = {k: resolve_secret(v) or "" for k, v in values.items()}
    return with_secrets(cast(MCPConfig, prepared), state_dir)


def _substitute_secrets(
    connector: str, field: str, value: str, host: str | None, state_dir: Path | None
) -> str:
    names = placeholders_in(value)
    if not names:
        return value
    if state_dir is None:
        raise SecretError(
            f"{connector}: {field} uses a secret, but there is nowhere to look it up."
        )
    store = SecretStore(state_dir)
    values: dict[str, str] = {}
    for name in sorted(names):
        hosts = store.hosts_of(name)
        if hosts is None:
            raise SecretError(f"{connector}: there is no secret named {name}.")
        if host is not None and not any(host_allowed(pattern, host) for pattern in hosts):
            raise SecretError(
                f"{connector}: the secret {name} may only be sent to "
                f"{', '.join(hosts)}, not {host}."
            )
        values[name] = store.resolve(name)
    return substitute(value, values)


def secret_names_used(entry: dict[str, Any]) -> set[str]:
    """The secrets a stored connector entry refers to, for refusing to delete
    one that is in use."""
    names: set[str] = set()
    for field in ("env", "headers"):
        for value in (entry.get(field) or {}).values():
            if isinstance(value, str):
                names |= placeholders_in(value)
    return names


def load_mcp_server_configs(
    config_path: Path, state_dir: Path | None = None, *, fill_secrets: bool = True
) -> dict[str, MCPConfig]:
    """Parse a Claude-Desktop-style {"mcpServers": {name: {...}}} JSON file into
    validated MCPConfig dicts, keyed by server name (`fill_secrets=False` keeps
    `{{secret:NAME}}` placeholders as written, for showing a connector). Each `env`/`headers`
    value is resolved via runtime/secrets.py's resolve_secret before being
    returned (not inside validate_mcp_config itself, since that function is
    also called directly on live, already-plaintext request payloads in
    web/app.py's add_mcp_server, where nothing needs resolving) -- so every
    caller here keeps getting a plain string regardless of whether it's
    stored on disk as a keyring reference or (legacy, or keyring
    unavailable) plaintext."""
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    servers = raw.get("mcpServers")
    if not isinstance(servers, dict):
        raise ValueError(f'{config_path}: expected a top-level "mcpServers" object')
    result: dict[str, MCPConfig] = {}
    for name, entry in servers.items():
        config = validate_mcp_config({"type": "mcp", "name": name, **entry})
        if "env" in config:
            config["env"] = {k: resolve_secret(v) or "" for k, v in config["env"].items()}
        if "headers" in config:
            config["headers"] = {k: resolve_secret(v) or "" for k, v in config["headers"].items()}
        try:
            result[name] = with_secrets(config, state_dir) if fill_secrets else config
        except SecretError as exc:
            # One connector's missing secret must not take the others down.
            logger.warning("Skipping connector %s: %s", name, exc)
    return result

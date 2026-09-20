"""MCP integration for runtime_lg -- via langchain-mcp-adapters instead of
tools/mcp.py's aisuite-based MCPClient.

**Why a separate connector, not a runtime_lg-side reuse of
tools/connect_mcp_tools:** aisuite's MCPToolWrapper collapses each tool's
real, detailed JSON Schema into a generic Python signature (e.g. a
List[dict] parameter with no indication of its required nested keys) for
its own __call__'s sake -- fine for this project's own Runner, which reads
the wrapper's separately-kept __mcp_input_schema__ directly, but LangChain's
create_agent has no knowledge that attribute exists, only inspect.signature().
Confirmed live (see runtime_lg/README.md's "MCP tools under runtime_lg"
section): a real model call guessed a wrong field name against the lossy
schema, got an MCP validation error back, and looped on the identical wrong
call rather than correcting.

langchain-mcp-adapters' convert_mcp_tool_to_langchain_tool builds each
StructuredTool with args_schema=tool.inputSchema directly -- the MCP
server's real raw JSON Schema, no lossy round-trip -- which is why this
module exists rather than patching tools/mcp.py's wrapper further. Reuses
tools/mcp.py's own config *parsing/validation* (load_mcp_server_configs) --
that half was never the broken part, only aisuite's tool-wrapping was.

**Persistent sessions, not "a fresh session per tool call" -- a real bug
found via live testing, not a design choice this module started with.**
MultiServerMCPClient.get_tools()'s own docstring says plainly: "A new
session will be created for each tool call." For a stateless tool (read a
file, look something up) that's invisible. For a *stateful* tool -- above
all browser automation (`@playwright/mcp`'s navigate/click/fill/screenshot
sequence, which only makes sense against the *same* open browser/page
across calls) -- it's fatal: every single call was spawning a brand-new
`npx @playwright/mcp` subprocess and a brand-new Chromium, running exactly
one action, then tearing the whole thing down the instant that call
returned. Live-reported symptom: a page opened by `playwright_browser_
navigate` closed again immediately (the process backing it exited right
after that one call), a fresh navigate+launch took on the order of a
minute on real hardware (a cold Chromium launch happening on *every single
tool call*), and any multi-step flow (navigate, then click, then fill,
then submit) was simply impossible -- each step started over with no
memory of the last. `coscribe-web`'s aisuite-based `tools/connect_
mcp_tools` never had this problem: its `MCPClient` is one persistent
client/subprocess per server, alive for the app's lifetime.

Fixed by `McpServerConnection` below: instead of `MultiServerMCPClient.
get_tools()`'s per-call `connection=` path, it opens one persistent
`ClientSession` via `client.session(name)` and keeps it open in a
dedicated background `asyncio.Task` for as long as this server is
"connected" (app lifespan, or until a user removes/reconnects it in the
Connectors panel) -- `load_mcp_tools(session, ...)` builds tools bound to
that *session object* rather than a fresh connection, so every call after
the first reuses the same subprocess/browser. The dedicated task exists
specifically so the session's own async context manager is entered *and*
exited by the same asyncio task throughout its life: anyio's stdio
transport ties internal task-group-scoped resources to whichever task
opened them, so entering during one request-handling task (e.g. `POST
/api/mcp/servers`) and later exiting from a *different* one (a later
`DELETE`, or app shutdown) would raise "Attempted to exit cancel scope in
a different task than it was entered in" -- a background task signaled by
a plain `asyncio.Event` sidesteps that entirely, since both `async with
client.session(...)` and its exit happen inside `_run()`'s own task.

One accepted trade-off, matching `coscribe-web`'s existing behavior
rather than introducing a new limitation: a persistent session (and
therefore a persistent browser, for Playwright) is shared by *every*
ChatSessionLG/thread in this process, same as aisuite's `MCPClient` already
is today for the old runtime -- two unrelated conversations both using
`playwright_browser_navigate` would drive the *same* browser/tabs. Not
solved here; solving it would mean a session-per-thread pool, real added
complexity for a problem this pass wasn't asked to fix.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.sessions import Connection
from langchain_mcp_adapters.tools import load_mcp_tools

from ..runtime.types import tool_metadata
from ..tools.mcp import load_mcp_server_configs

logger = logging.getLogger(__name__)

# Bounds connect() below -- unlike MCP_STARTUP_TIMEOUT_SECONDS in web/app.py,
# which only bounds how long *app startup* waits, this applies to every
# connect attempt (startup and live Connectors-panel Add/reconnect alike).
# 60s because a cold `npx` package-manager resolve plus a real MCP
# initialize handshake can legitimately take tens of seconds on a slow
# connection.
_CONNECT_TIMEOUT_SECONDS = 60.0


def _forwarded_network_env() -> dict[str, str]:
    """Real, confirmed gap this closes: the `mcp` SDK's stdio client
    (`mcp.client.stdio.get_default_environment`) deliberately passes a
    spawned server only a small OS-essentials allowlist (on Windows:
    APPDATA/HOMEDRIVE/HOMEPATH/LOCALAPPDATA/PATH/PATHEXT/
    PROCESSOR_ARCHITECTURE/SYSTEMDRIVE/SYSTEMROOT/TEMP/USERNAME/
    USERPROFILE -- confirmed by reading that function's actual source),
    not the coscribe-web process's own environment -- so a corporate
    HTTP_PROXY/HTTPS_PROXY, or the extra CA-trust env vars a TLS-
    intercepting proxy needs (Node and uv each have their own separate
    certificate store, neither of which reads the OS trust store by
    default), never reaches `npx`/`uvx` regardless of what's set in
    coscribe's own `.env` or real Windows system environment variables.
    Every catalog entry hits this the same way (npx- and uvx-based
    alike), matching the real, live-reported symptom: every connector on
    a corporate network fails to connect, not just one.

    Forwards only these specific, known-relevant vars (both cases, since
    different tools check different casing) rather than the whole
    process environment -- the SDK's own curated allowlist is a
    deliberate security choice for arbitrary third-party MCP servers,
    and this isn't the place to override that wholesale."""
    names = (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "NODE_EXTRA_CA_CERTS",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "UV_NATIVE_TLS",
    )
    forwarded: dict[str, str] = {}
    for name in names:
        for key in (name, name.lower()):
            value = os.environ.get(key)
            if value is not None:
                forwarded[key] = value
    return forwarded


def _to_lg_connection(config: Mapping[str, Any]) -> Connection:
    """Translate one of tools/mcp.py's already-validated MCPConfig entries
    into the shape MultiServerMCPClient expects. Only the two transports
    tools/mcp.py's own catalog ever configures (see web/app.py's
    MCP_CATALOG: npx/uvx stdio commands, or a bare server_url) are handled;
    unlike aisuite's config, langchain-mcp-adapters requires an explicit
    "transport" key rather than inferring it, so that inference happens
    here instead of pushing a new required field onto every existing
    mcp.json."""
    if "command" in config:
        # Resolved via shutil.which rather than passed through as a bare
        # name -- real, Windows-specific bug: `npx`/`uvx` there are
        # `.cmd`/`.bat` wrapper scripts, and Windows' raw CreateProcess
        # (what asyncio/anyio's subprocess spawning uses under the hood,
        # not a shell) doesn't search PATHEXT extensions the way typing
        # the bare name at a real command prompt does -- it fails with
        # `FileNotFoundError: [WinError 2]` even though the same command
        # works fine when the user runs it themselves. shutil.which
        # already does this PATHEXT-aware resolution correctly on Windows
        # (returning e.g. `...\npx.cmd`), and CreateProcess *does* know
        # how to launch a `.cmd`/`.bat` file when given its full path
        # (auto-invokes it via cmd.exe) -- the bug was purely in *finding*
        # the file, not running it once found. A no-op on Linux/macOS,
        # where the bare command already resolved correctly.
        #
        # Genuinely missing (shutil.which returns None -- the command
        # isn't installed at all, not just a PATHEXT quirk) is a separate
        # case: falling through to the bare name used to let it reach the
        # actual subprocess spawn, which fails with an opaque `[WinError
        # 2] The system cannot find the file specified` -- correct, but
        # useless to a user with no reason to know what that code means.
        # Raise a clear message instead, naming what to install.
        raw_command = config["command"]
        command = shutil.which(raw_command)
        if command is None:
            install_hint = {
                "npx": "install Node.js (https://nodejs.org)",
                "uvx": "install uv (https://docs.astral.sh/uv/)",
            }.get(raw_command, f"install {raw_command!r} and make sure it's on PATH")
            raise FileNotFoundError(
                f"{raw_command!r} isn't installed (or isn't on PATH) -- {install_hint}, "
                "then restart coscribe."
            )
        connection: dict[str, Any] = {
            "transport": "stdio",
            "command": command,
            "args": config.get("args", []),
        }
        # Always set, even if empty -- a server-specific env (e.g.
        # Slack's SLACK_BOT_TOKEN) wins on overlap, but the network vars
        # above apply to every stdio server, not just ones that happen to
        # already declare their own env in mcp.json.
        connection["env"] = {**_forwarded_network_env(), **config.get("env", {})}
        if "cwd" in config:
            connection["cwd"] = config["cwd"]
        return cast("Connection", connection)
    connection = {"transport": "streamable_http", "url": config["server_url"]}
    if "headers" in config:
        connection["headers"] = config["headers"]
    return cast("Connection", connection)


def _strip_boolean_enums(schema: Any) -> None:
    """Mutates an MCP tool's raw JSON Schema in place, deleting any `enum`
    key whose values are booleans rather than strings (e.g. `{"type":
    "boolean", "enum": [true, false]}`) -- a real, live-reported crash:
    `google-genai`'s own Schema conversion expects every `enum` value to be
    a string (its error, verbatim: "Failed to parse enum field: expected
    string or bytes-like object, got 'bool'"), and OpenAPI-generated
    servers with many boolean parameters (confirmed live for GitHub's own
    remote MCP server, api.githubcopilot.com/mcp/ -- a common OpenAPI-
    codegen pattern, not something coscribe's own tools ever produce) emit
    exactly this redundant-but-technically-valid shape. Dropping the `enum`
    loses nothing: `"type": "boolean"` alone already fully constrains the
    value. Same "sanitize an external schema for a real Gemini-specific
    incompatibility" category of fix as this module's `X | None` bug --
    only reachable via load_mcp_tools's raw, unmodified `tool.inputSchema`
    (see this module's own docstring for why MCP tools keep the server's
    real schema, unlike coscribe's own Python-function-derived tools,
    which never produce a boolean enum in the first place)."""
    if isinstance(schema, dict):
        enum = schema.get("enum")
        if isinstance(enum, list) and any(isinstance(v, bool) for v in enum):
            del schema["enum"]
        for value in schema.values():
            _strip_boolean_enums(value)
    elif isinstance(schema, list):
        for item in schema:
            _strip_boolean_enums(item)


def _default_missing_array_items(schema: Any) -> None:
    """Mutates an MCP tool's raw JSON Schema in place, filling in a
    `{"type": "string"}` `items` schema for any property declared `"type":
    "array"` with no `items` key at all -- valid, common JSON Schema
    (plain JSON Schema treats a missing `items` as "array of anything"),
    but would be a crash for `google-genai`'s own Schema conversion, whose
    proto-based Schema type requires `items` to always be present for an
    array: `400 * GenerateContentRequest.tools[...].function_
    declarations[...].parameters.properties[...].items: missing field`.
    Kept as a defensive catch-all even though the one real, live-reported
    instance of this exact error message turned out to have a different
    root cause entirely (see `_collapse_non_nullable_anyof` below, found
    by bisecting GitHub's real remote MCP server down to the exact tool)
    -- this shape is still possible in principle from some other server,
    and costs nothing to guard against. `"string"` is a guess at the most
    common case, not a claim it's always correct -- filling in *something*
    valid is what avoids the crash."""
    if isinstance(schema, dict):
        schema_type = schema.get("type")
        is_array = schema_type == "array" or (
            isinstance(schema_type, list) and "array" in schema_type
        )
        if is_array and "items" not in schema:
            schema["items"] = {"type": "string"}
        for value in schema.values():
            _default_missing_array_items(value)
    elif isinstance(schema, list):
        for item in schema:
            _default_missing_array_items(item)


def _collapse_non_nullable_anyof(schema: Any) -> None:
    """Mutates an MCP tool's raw JSON Schema in place, collapsing a
    non-nullable `anyOf` (a real union type, e.g. "a string, or an array
    of strings" -- distinct from the nullable `X | None` pattern, an
    `anyOf` where one branch is `{"type": "null"}`, which google-genai
    already handles correctly) down to a single branch.

    Real, live-reported crash, root-caused by bisecting GitHub's real
    remote MCP server (api.githubcopilot.com/mcp/) down to the exact tool
    (`run_secret_scanning`'s `files` parameter,
    `{"anyOf": [{"type": "string", ...}, {"type": "array", "items": {...},
    ...}]}`) and reading `langchain_google_genai`'s own conversion source
    (`_function_utils.py`) to find the actual bug: for a non-nullable
    `anyOf`, that code picks an overall `type_` for the property (here,
    `ARRAY`, since one branch is an array) but only ever looks for `items`
    on the *original, unmodified* property object -- which has no
    top-level `items` key at all, since the real one lives one level down
    inside the array branch of `anyOf`. The nullable-`anyOf` code path
    (a few lines away in the same function) *does* correctly thread
    `items` through from the chosen branch; this one doesn't. Net effect:
    `type_: ARRAY` gets set with no `items` alongside it, reproducing
    exactly `400 * ...properties[files].items: missing field` -- verified
    directly by copying this real schema into a throwaway tool and making
    a real Gemini call, both before this fix (reproduces) and after
    (succeeds).

    Not fixable by filling in a plausible `items` value the way
    `_default_missing_array_items` does above -- the bug is in *how the
    library converts* an already-well-formed schema, not in the schema
    itself (the array branch's own `items` is perfectly valid on its own).
    The only way to route around a conversion bug in code we don't own is
    to never hand it the shape that triggers it: collapse `anyOf` to one
    branch before it ever reaches `convert_to_genai_function_declarations`.
    Prefers an array branch (most permissive), then an object branch, then
    just the first branch -- picking *a* valid, usable type is what
    matters here, not preserving perfect fidelity to a union type Gemini's
    schema format has no way to express at all."""
    if isinstance(schema, dict):
        any_of = schema.get("anyOf")
        if (
            isinstance(any_of, list)
            and any_of
            and all(isinstance(branch, dict) and branch.get("type") != "null" for branch in any_of)
        ):
            chosen = next((b for b in any_of if b.get("type") == "array"), None)
            if chosen is None:
                chosen = next((b for b in any_of if b.get("type") == "object"), None)
            if chosen is None:
                chosen = any_of[0]
            merged = dict(chosen)
            if "description" not in merged and "description" in schema:
                merged["description"] = schema["description"]
            schema.clear()
            schema.update(merged)
            _collapse_non_nullable_anyof(schema)
            return
        for value in schema.values():
            _collapse_non_nullable_anyof(value)
    elif isinstance(schema, list):
        for item in schema:
            _collapse_non_nullable_anyof(item)


class McpServerConnection:
    """Owns one MCP server's persistent session -- see module docstring for
    why this exists instead of MultiServerMCPClient.get_tools()'s default
    per-call session. `connect()` waits for the background task to either
    finish connecting (returning the tagged tool list) or fail (re-raising
    whatever the connection attempt raised); `close()` signals the
    background task to exit its `async with client.session(...)` block and
    waits for it to actually finish, so the underlying subprocess is
    genuinely gone by the time `close()` returns, not just "asked to
    leave." Safe to `close()` more than once or before `connect()` ever
    succeeded."""

    def __init__(self, name: str, config: Mapping[str, Any]) -> None:
        self.name = name
        self._config = config
        self._stop = asyncio.Event()
        self._ready = asyncio.Event()
        self._tools: list[BaseTool] = []
        self._error: BaseException | None = None
        self._task: asyncio.Task[None] = asyncio.create_task(
            self._run(), name=f"mcp-session-{name}"
        )

    async def _run(self) -> None:
        try:
            client = MultiServerMCPClient(
                {self.name: _to_lg_connection(self._config)}, tool_name_prefix=True
            )
            async with client.session(self.name) as session:
                tools = await load_mcp_tools(
                    session, server_name=self.name, tool_name_prefix=True
                )
                for tool in tools:
                    # See _strip_boolean_enums'/_collapse_non_nullable_
                    # anyof's/_default_missing_array_items' own docstrings
                    # -- real crashes with GitHub's remote MCP server's own
                    # schemas, not hypotheticals. Collapse runs first: it
                    # can turn an anyOf branch into a clean, already-valid
                    # array (with its own real items), so there's nothing
                    # left for the missing-items filler to (wrongly) guess
                    # at afterward.
                    args_schema = getattr(tool, "args_schema", None)
                    _strip_boolean_enums(args_schema)
                    _collapse_non_nullable_anyof(args_schema)
                    _default_missing_array_items(args_schema)
                    # tool_metadata's own signature only names Callable, but
                    # it's really just setattr(func, TOOL_METADATA_ATTR, ...)
                    # underneath -- genuinely safe on a BaseTool too, same
                    # reasoning as agent.py's identical cast.
                    tool_metadata(
                        cast(Callable[..., Any], tool),
                        risk_category="EXTERNAL",
                        category=f"mcp:{self.name}",
                    )
                self._tools = tools
                self._ready.set()
                await self._stop.wait()
        except Exception as exc:  # noqa: BLE001 -- surfaced to connect() below, not swallowed
            self._error = exc
            self._ready.set()

    async def connect(self) -> list[BaseTool]:
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=_CONNECT_TIMEOUT_SECONDS)
        except TimeoutError:
            raise TimeoutError(
                f"Timed out after {_CONNECT_TIMEOUT_SECONDS:.0f}s waiting for "
                f"{self.name!r} to connect -- the server process may be stuck "
                "(a stalled package download, no network access, ...)"
            ) from None
        if self._error is not None:
            raise self._error
        return self._tools

    async def close(self) -> None:
        if self._task.done():
            return
        self._stop.set()
        await self._task


async def connect_one_mcp_server_lg(
    name: str, config: Mapping[str, Any]
) -> tuple[list[BaseTool], McpServerConnection | None, str | None]:
    """Connect a single already-validated server config, returning its
    tagged LangChain tools plus the McpServerConnection backing them (None
    on failure -- nothing to close), plus a human-readable error message
    (None on success). Failure is logged rather than raised, same
    tolerance as tools/connect_one_mcp_server so one bad server config
    doesn't take down every other configured server. Only add_mcp_server
    (web/app.py) surfaces the error string to a response today;
    connect_mcp_tools_lg's own startup path below still only logs it,
    matching that path's "one broken server can't block the others"
    posture.

    Unlike before this module's persistent-session fix, the caller now
    *must* eventually call the returned connection's close() once this
    server should disconnect (a Connectors-panel removal/reconnect, or app
    shutdown) -- otherwise its subprocess (and, for Playwright, its
    browser) leaks past that point. See app.py's mcp_connections holder.
    """
    connection = McpServerConnection(name, config)
    try:
        tools = await connection.connect()
    except Exception as exc:
        logger.warning("Skipping MCP server %r: failed to connect", name, exc_info=True)
        # cancel(), not close(): close() waits for _run() to unwind via
        # self._stop, but a timeout means _run() is stuck inside the
        # connect attempt itself, never reaching that checkpoint -- close()
        # would hang. cancel() is safe unconditionally.
        connection._task.cancel()  # noqa: SLF001 -- best-effort cleanup, not a public API
        return [], None, str(exc) or type(exc).__name__
    return tools, connection, None


async def connect_mcp_tools_lg(
    config_path: Path,
) -> tuple[list[BaseTool], dict[str, McpServerConnection]]:
    """Connect to every configured MCP server and return the combined,
    tagged tool list plus a {name: connection} map (for the caller to close
    at shutdown) -- the runtime_lg counterpart to tools/connect_mcp_tools,
    called the same way from cli.py/app.py.

    A missing config file is tolerated (empty list/map), matching
    tools/connect_mcp_tools's same startup-robustness contract.

    Connects to every server **concurrently**, not one at a time -- a real,
    live-reported startup-latency bug: each connection can involve a cold
    `npx`/`uvx` subprocess spawn plus the MCP initialize handshake (easily
    several seconds each on Windows), and awaiting them one by one in a
    plain loop made total startup time grow linearly with the number of
    configured servers. connect_one_mcp_server_lg already tolerates and
    logs a single server's own failure without raising (see its own
    docstring), so gathering them concurrently doesn't change that
    contract -- one slow/broken server still can't block the others, it
    just no longer blocks them *serially* either.
    """
    if not config_path.is_file():
        logger.warning(
            "MCP config file not found at %s -- starting with no MCP tools", config_path
        )
        return [], {}
    configs = load_mcp_server_configs(config_path)
    results = await asyncio.gather(
        *(connect_one_mcp_server_lg(name, config) for name, config in configs.items())
    )
    tools: list[BaseTool] = []
    connections: dict[str, McpServerConnection] = {}
    for name, (server_tools, connection, _error) in zip(configs, results, strict=True):
        tools.extend(server_tools)
        if connection is not None:
            connections[name] = connection
    return tools, connections

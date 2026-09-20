"""Tests for runtime_lg/mcp.py -- the langchain-mcp-adapters-based MCP
connector runtime_lg uses in place of tools/mcp.py's aisuite-based one (see
that module's docstring for why). No real subprocess/network I/O:
MultiServerMCPClient/load_mcp_tools are monkeypatched with fakes that mimic
their session(name)/load_mcp_tools(session, ...) surface -- the *persistent*
session path (see McpServerConnection's own docstring for why a persistent
session, not MultiServerMCPClient.get_tools()'s per-call default) -- same
"no real server" discipline as tests/test_mcp_tools.py uses for the aisuite
path.

Needs the langgraph_spike extra installed -- skips cleanly via
importorskip rather than failing collection when it isn't present.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from langchain_core.tools import StructuredTool

from coscribe.runtime.types import get_tool_metadata
from coscribe.runtime_lg.mcp import (
    McpServerConnection,
    _collapse_non_nullable_anyof,
    _default_missing_array_items,
    _strip_boolean_enums,
    _to_lg_connection,
    connect_mcp_tools_lg,
    connect_one_mcp_server_lg,
)


def _write_config(tmp_path: Path, servers: dict[str, Any]) -> Path:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({"mcpServers": servers}))
    return config_path


def _fake_tool(name: str) -> StructuredTool:
    def _run(x: str = "") -> str:
        return x

    return StructuredTool.from_function(func=_run, name=name, description="fake tool")


class _FakeClientSession:
    """Stands in for the ClientSession McpServerConnection gets back from
    client.session(name) -- identity doesn't matter to anything under
    test, only that it's *a* session-shaped object load_mcp_tools can be
    called with."""


class _FakeMultiServerMCPClient:
    """Stands in for langchain_mcp_adapters.client.MultiServerMCPClient's
    persistent-session surface: session(name) as an async context manager,
    not get_tools() -- see McpServerConnection's own docstring for why this
    is the path runtime_lg/mcp.py actually uses now."""

    def __init__(self, connections: dict[str, Any], *, tool_name_prefix: bool = False) -> None:
        self.connections = connections
        self.tool_name_prefix = tool_name_prefix
        self.closed_names: list[str] = []

    @asynccontextmanager
    async def session(self, server_name: str, *, auto_initialize: bool = True):  # type: ignore[no-untyped-def]
        if server_name == "broken":
            raise RuntimeError("connection refused")
        if server_name == "hangs":
            # A session that spawns fine but never completes its handshake.
            await asyncio.sleep(3600)
        try:
            yield _FakeClientSession()
        finally:
            self.closed_names.append(server_name)


async def _fake_load_mcp_tools(
    session: Any, *, server_name: str | None = None, tool_name_prefix: bool = False, **_: Any
) -> list[StructuredTool]:
    assert isinstance(session, _FakeClientSession)
    assert server_name is not None
    base_name = f"{server_name}_tool"
    name = f"{server_name}_{base_name}" if tool_name_prefix else base_name
    return [_fake_tool(name)]


def _patch_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "coscribe.runtime_lg.mcp.MultiServerMCPClient", _FakeMultiServerMCPClient
    )
    monkeypatch.setattr("coscribe.runtime_lg.mcp.load_mcp_tools", _fake_load_mcp_tools)


async def test_connect_mcp_tools_lg_tags_tools_and_prefixes_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_mcp(monkeypatch)
    config_path = _write_config(tmp_path, {"fs": {"command": "npx", "args": []}})

    tools, connections = await connect_mcp_tools_lg(config_path)

    assert len(tools) == 1
    tool = tools[0]
    assert tool.name == "fs_fs_tool"
    metadata = get_tool_metadata(tool)  # type: ignore[arg-type]
    assert metadata.risk_category == "EXTERNAL"
    assert metadata.requires_approval is True
    assert metadata.category == "mcp:fs"
    assert set(connections) == {"fs"}
    await connections["fs"].close()


async def test_connect_mcp_tools_lg_skips_broken_server_but_keeps_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_mcp(monkeypatch)
    config_path = _write_config(
        tmp_path,
        {"broken": {"command": "npx"}, "fs": {"command": "npx", "args": []}},
    )

    tools, connections = await connect_mcp_tools_lg(config_path)

    assert [t.name for t in tools] == ["fs_fs_tool"]
    assert set(connections) == {"fs"}
    await connections["fs"].close()


async def test_connect_mcp_tools_lg_tolerates_a_missing_config_file(tmp_path: Path) -> None:
    missing_path = tmp_path / "does-not-exist.json"

    tools, connections = await connect_mcp_tools_lg(missing_path)

    assert tools == []
    assert connections == {}


async def test_connect_one_mcp_server_lg_returns_empty_list_on_connect_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mcp(monkeypatch)

    tools, connection, error = await connect_one_mcp_server_lg(
        "broken", {"command": "npx", "args": []}
    )

    assert tools == []
    assert connection is None
    assert error == "connection refused"


async def test_connect_one_mcp_server_lg_returns_a_closeable_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mcp(monkeypatch)

    tools, connection, error = await connect_one_mcp_server_lg("fs", {"command": "npx", "args": []})
    assert error is None

    assert len(tools) == 1
    assert connection is not None
    await connection.close()
    # close() must actually wait for the background task to unwind the
    # session's own async context manager, not just fire-and-forget --
    # otherwise a caller closing right before disconnecting couldn't rely
    # on the underlying subprocess actually being gone yet (see
    # McpServerConnection's own docstring).
    assert connection._task.done()  # noqa: SLF001 -- whitebox check, this test's whole point


async def test_connect_one_mcp_server_lg_reports_a_clear_error_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shrinks the module's real connect-timeout constant rather than
    actually waiting 60s for this test."""
    _patch_mcp(monkeypatch)
    monkeypatch.setattr("coscribe.runtime_lg.mcp._CONNECT_TIMEOUT_SECONDS", 0.05)

    tools, connection, error = await connect_one_mcp_server_lg(
        "hangs", {"command": "npx", "args": []}
    )

    assert tools == []
    assert connection is None
    assert error is not None
    assert "timed out" in error.lower()
    assert "hangs" in error


async def test_mcp_server_connection_connect_times_out_instead_of_hanging_forever(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitebox counterpart to the test above: connect() raises on timeout,
    and the background task is still running afterward (proving a graceful
    close() would hang, and cancel() is the only safe cleanup)."""
    _patch_mcp(monkeypatch)
    monkeypatch.setattr("coscribe.runtime_lg.mcp._CONNECT_TIMEOUT_SECONDS", 0.05)
    connection = McpServerConnection("hangs", {"command": "npx", "args": []})

    with pytest.raises(TimeoutError):
        await connection.connect()

    assert not connection._task.done()  # noqa: SLF001 -- whitebox, this test's whole point

    connection._task.cancel()  # noqa: SLF001
    with pytest.raises(asyncio.CancelledError):
        await connection._task  # noqa: SLF001
    assert connection._task.done()  # noqa: SLF001


async def test_mcp_server_connection_close_is_safe_to_call_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_mcp(monkeypatch)
    connection = McpServerConnection("fs", {"command": "npx", "args": []})
    await connection.connect()

    await connection.close()
    await connection.close()  # must not raise/hang the second time


def test_to_lg_connection_stdio_resolves_command_via_shutil_which(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for a real, live-reported bug: on Windows, `npx`/
    `uvx` are .cmd/.bat wrapper scripts, and the raw CreateProcess call
    asyncio/anyio's subprocess spawning uses under the hood doesn't search
    PATHEXT extensions the way a real command prompt does -- passing the
    bare name failed with FileNotFoundError even though the command works
    fine when the user runs it themselves. Resolving via shutil.which
    (which does PATHEXT-aware resolution) before handing the command to
    MultiServerMCPClient fixes it."""
    monkeypatch.setattr(
        "coscribe.runtime_lg.mcp.shutil.which",
        lambda name: "C:\\Program Files\\nodejs\\npx.cmd" if name == "npx" else None,
    )
    # Deterministic regardless of what's actually set in the environment
    # this test happens to run in -- see the dedicated
    # test_to_lg_connection_forwards_corporate_network_env below for the
    # forwarding behavior itself.
    monkeypatch.setattr("coscribe.runtime_lg.mcp._forwarded_network_env", dict)

    connection = _to_lg_connection(
        {"command": "npx", "args": ["@scope/pkg"], "env": {"FOO": "bar"}, "cwd": "/tmp"}
    )

    assert connection == {
        "transport": "stdio",
        "command": "C:\\Program Files\\nodejs\\npx.cmd",
        "args": ["@scope/pkg"],
        "env": {"FOO": "bar"},
        "cwd": "/tmp",
    }


def test_to_lg_connection_forwards_corporate_network_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for a real, confirmed gap: the underlying `mcp`
    SDK's stdio client only passes a spawned server a small OS-essentials
    allowlist (confirmed by reading mcp.client.stdio.
    get_default_environment's actual source) -- HTTP_PROXY/HTTPS_PROXY
    and the CA-trust env vars a corporate TLS-intercepting proxy needs
    never reached npx/uvx regardless of what coscribe's own .env set,
    explaining a real, live-reported symptom: every MCP connector failing
    on a corporate network, not just one. Also confirms an explicit
    per-server env (e.g. Slack's token) still wins over a same-named
    forwarded var."""
    monkeypatch.setattr("coscribe.runtime_lg.mcp.shutil.which", lambda name: name)
    # This sandbox's own outbound HTTPS setup already sets several of
    # these (SSL_CERT_FILE/REQUESTS_CA_BUNDLE/UV_NATIVE_TLS/lowercase
    # https_proxy/no_proxy) -- clear every name this function forwards,
    # both cases, so the test is deterministic regardless of ambient
    # environment.
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "NODE_EXTRA_CA_CERTS",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "UV_NATIVE_TLS",
    ):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.corp.example:8080")
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "C:\\corp-ca.pem")
    monkeypatch.setenv("SOME_UNRELATED_VAR", "should-not-be-forwarded")

    connection = _to_lg_connection({"command": "npx", "args": []})

    assert connection["env"] == {
        "HTTPS_PROXY": "http://proxy.corp.example:8080",
        "NODE_EXTRA_CA_CERTS": "C:\\corp-ca.pem",
    }

    # An explicit per-server env wins over a same-named forwarded var.
    monkeypatch.setenv("NODE_EXTRA_CA_CERTS", "C:\\corp-ca.pem")
    connection = _to_lg_connection(
        {"command": "npx", "args": [], "env": {"NODE_EXTRA_CA_CERTS": "C:\\overridden.pem"}}
    )
    assert connection["env"]["NODE_EXTRA_CA_CERTS"] == "C:\\overridden.pem"


def test_to_lg_connection_raises_a_clear_error_when_the_command_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When shutil.which can't find the command at all (genuinely not
    installed, not just a PATHEXT quirk), a real, live-reported gap: this
    used to fall through to the bare command name, which reached the
    actual subprocess spawn and failed with an opaque `[WinError 2] The
    system cannot find the file specified` -- correct, but useless to a
    user with no reason to know what that code means. Now raises with the
    install instructions instead."""
    monkeypatch.setattr("coscribe.runtime_lg.mcp.shutil.which", lambda name: None)

    with pytest.raises(FileNotFoundError, match="uv"):
        _to_lg_connection({"command": "uvx", "args": []})

    with pytest.raises(FileNotFoundError, match="Node.js"):
        _to_lg_connection({"command": "npx", "args": []})

    with pytest.raises(FileNotFoundError, match="some-other-command"):
        _to_lg_connection({"command": "some-other-command", "args": []})


def test_to_lg_connection_streamable_http() -> None:
    connection = _to_lg_connection(
        {"server_url": "https://example.com/mcp", "headers": {"Authorization": "Bearer x"}}
    )

    assert connection == {
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer x"},
    }


def test_strip_boolean_enums_removes_a_boolean_valued_enum() -> None:
    """Regression test for a real, live-reported crash: google-genai's own
    Schema conversion rejects an `enum` whose values are booleans rather
    than strings ("Failed to parse enum field: expected string or
    bytes-like object, got 'bool'") -- confirmed live against GitHub's own
    remote MCP server, an OpenAPI-codegen pattern that emits exactly this
    redundant-but-valid shape for boolean parameters."""
    schema = {
        "type": "object",
        "properties": {
            "draft": {"type": "boolean", "enum": [True, False]},
            "title": {"type": "string", "enum": ["a", "b"]},
        },
    }

    _strip_boolean_enums(schema)

    assert "enum" not in schema["properties"]["draft"]
    assert schema["properties"]["draft"]["type"] == "boolean"
    assert schema["properties"]["title"]["enum"] == ["a", "b"]  # untouched


def test_strip_boolean_enums_walks_nested_objects_and_arrays() -> None:
    schema = {
        "type": "object",
        "properties": {
            "filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"archived": {"type": "boolean", "enum": [False]}},
                },
            }
        },
    }

    _strip_boolean_enums(schema)

    assert "enum" not in schema["properties"]["filters"]["items"]["properties"]["archived"]


def test_strip_boolean_enums_tolerates_none_and_non_dict_input() -> None:
    _strip_boolean_enums(None)  # must not raise -- args_schema can be a pydantic model, not a dict
    _strip_boolean_enums("not a schema")


async def test_connect_one_mcp_server_lg_strips_boolean_enums_from_the_real_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end proof, not just the pure function in isolation: a tool
    coming back from load_mcp_tools with a raw dict args_schema (exactly
    what MCP-derived tools get, per this module's own docstring) has its
    boolean enums stripped before connect_one_mcp_server_lg returns it."""
    _patch_mcp(monkeypatch)
    bad_schema = {
        "type": "object",
        "properties": {"pinned": {"type": "boolean", "enum": [True, False]}},
    }
    tool_with_raw_schema = StructuredTool(
        name="github_issue_update",
        description="fake",
        args_schema=bad_schema,
        func=lambda **kwargs: "",
    )

    async def _fake_load_mcp_tools_with_bad_schema(
        session: Any, *, server_name: str | None = None, tool_name_prefix: bool = False, **_: Any
    ) -> list[StructuredTool]:
        return [tool_with_raw_schema]

    monkeypatch.setattr(
        "coscribe.runtime_lg.mcp.load_mcp_tools", _fake_load_mcp_tools_with_bad_schema
    )

    tools, connection, error = await connect_one_mcp_server_lg(
        "github", {"command": "npx", "args": []}
    )

    assert error is None
    assert "enum" not in tools[0].args_schema["properties"]["pinned"]  # type: ignore[index]
    assert connection is not None
    await connection.close()


def test_default_missing_array_items_fills_in_a_string_default() -> None:
    """Defensive catch-all, not itself the confirmed root cause of the
    live-reported "...items: missing field" crash (see
    _collapse_non_nullable_anyof below for that) -- valid JSON Schema
    allows `{"type": "array"}` with no `items` at all (meaning "array of
    anything"), which would still crash google-genai's proto-based
    Schema the same way if some other server ever emitted it."""
    schema = {
        "type": "object",
        "properties": {
            "files": {"type": "array", "description": "paths to attach"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }

    _default_missing_array_items(schema)

    assert schema["properties"]["files"]["items"] == {"type": "string"}
    assert schema["properties"]["files"]["description"] == "paths to attach"  # untouched
    assert schema["properties"]["tags"]["items"] == {"type": "string"}  # already valid, untouched


def test_default_missing_array_items_handles_a_type_list() -> None:
    schema = {"type": ["array", "null"]}

    _default_missing_array_items(schema)

    assert schema["items"] == {"type": "string"}


def test_default_missing_array_items_tolerates_none_and_non_dict_input() -> None:
    _default_missing_array_items(None)
    _default_missing_array_items("not a schema")


def test_collapse_non_nullable_anyof_prefers_the_array_branch() -> None:
    """Regression test for the real, live-reported crash, root-caused by
    bisecting GitHub's real remote MCP server down to the exact tool
    (run_secret_scanning's `files` parameter) and reading langchain_
    google_genai's own conversion source: for a non-nullable `anyOf` (a
    real union type -- distinct from the nullable `X | None` pattern,
    which that library already handles correctly), it sets an overall
    `type_` (here, ARRAY, since one branch is an array) but never
    threads `items` through from the chosen branch, reproducing "400 *
    ...properties[files].items: missing field" -- confirmed directly by
    copying this exact real schema into a throwaway tool and making a
    real Gemini call, both before this fix (reproduced) and after
    (succeeded). Verified live -- see scripts/bisect_github_schema_issue.py
    for how this was found."""
    schema = {
        "type": "object",
        "properties": {
            "files": {
                "description": "A single string or an array of strings.",
                "anyOf": [
                    {"type": "string", "minLength": 1},
                    {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100},
                ],
            },
            "owner": {"type": "string", "description": "Repository owner"},
        },
        "required": ["files", "owner"],
    }

    _collapse_non_nullable_anyof(schema)

    files_schema = schema["properties"]["files"]  # type: ignore[index]
    assert files_schema == {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "maxItems": 100,
        "description": "A single string or an array of strings.",
    }
    assert "anyOf" not in files_schema
    assert schema["properties"]["owner"] == {"type": "string", "description": "Repository owner"}  # type: ignore[index]


def test_collapse_non_nullable_anyof_leaves_nullable_anyof_alone() -> None:
    """A nullable anyOf (one branch is `{"type": "null"}`) is the `X |
    None` pattern google-genai already converts correctly -- collapsing
    it too would be an unrelated, unnecessary behavior change."""
    schema = {
        "properties": {
            "tags": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}
        }
    }

    _collapse_non_nullable_anyof(schema)

    assert "anyOf" in schema["properties"]["tags"]  # type: ignore[index]


def test_collapse_non_nullable_anyof_falls_back_to_object_then_first_branch() -> None:
    object_schema = {"anyOf": [{"type": "string"}, {"type": "object", "properties": {}}]}
    _collapse_non_nullable_anyof(object_schema)
    assert object_schema["type"] == "object"

    no_array_or_object = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    _collapse_non_nullable_anyof(no_array_or_object)
    assert no_array_or_object["type"] == "string"  # first branch, no array/object candidate


def test_collapse_non_nullable_anyof_tolerates_none_and_non_dict_input() -> None:
    _collapse_non_nullable_anyof(None)
    _collapse_non_nullable_anyof("not a schema")

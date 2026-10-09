# ruff: noqa: E402
"""Web tests: the connector routes (/api/mcp/*) and their sign-in."""

import asyncio
import contextlib
import json
import socket
import stat
import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from dotenv import dotenv_values
from langchain_core.messages import (
    AIMessage,
)

from .helpers import (
    FakeToolCallingChatModel,
    _client_lg,
    _install_fake_keyring,
    _receive_until,
    _tool_call,
)


def test_get_mcp_catalog_returns_curated_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/mcp/catalog")

    assert response.status_code == 200
    entries = response.json()
    names = {entry["name"] for entry in entries}
    assert {"canva", "notion", "miro", "monday", "atlassian", "clickup", "zapier"} <= names
    assert "playwright" not in names
    # What coscribe already covers itself (reading pages, memory, the
    # date) isn't offered as a connector, nor is anything that can't be
    # signed in to with one click (a local install, or an app someone must
    # register or approve first).
    left_out = {"fetch", "memory", "sequential-thinking", "time", "slack", "office365", "dropbox"}
    assert not names & left_out
    for entry in entries:
        assert entry["server_url"].startswith("https://")
        assert not {"command", "args", "env", "needs_config"} & entry.keys()
        assert entry["category"] and entry["about"]
        assert len(set(entry.get("tools", []))) == len(entry.get("tools", []))
        assert all(link["url"].startswith("https://") for link in entry.get("links", []))
    # Public documentation servers need no sign-in; every other one signs in.
    public = {e["name"] for e in entries if "auth" not in e}
    assert public == {"mslearn", "huggingface", "cloudflare"}
    assert all(e["auth"] == "oauth" for e in entries if e["name"] not in public)


def test_every_catalog_connector_has_its_own_icon() -> None:
    from coscribe.web.connector_catalog import MCP_CATALOG

    icons = Path(__file__).parent.parent.parent / "frontend" / "src" / "assets" / "connectors"
    have = {p.stem for p in icons.iterdir()}
    assert {entry["name"] for entry in MCP_CATALOG} <= have


def test_the_catalog_lists_the_tools_a_connector_reported_when_it_was_connected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The maker's documentation lists 46 Canva tools and the live server 48,
    and some services list none: what the service said wins, and is kept."""
    from coscribe.runtime_lg import mcp as lg_mcp
    from tests.oauth_mcp_server import running_oauth_mcp_server

    real_connect = lg_mcp.connect_one_mcp_server_lg
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    with (
        running_oauth_mcp_server() as server,
        _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client,
    ):
        monkeypatch.setattr("coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", real_connect)
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", _sign_in_like_a_browser(client, opened))
        before = {e["name"]: e for e in client.get("/api/mcp/catalog").json()}["canva"]
        assert before["tools_from"] == "docs" and len(before["tools"]) == 46

        client.post(
            "/api/mcp/servers",
            json={"name": "canva", "server_url": server.mcp_url, "auth": "oauth"},
        )
        _wait_until(lambda: client.get("/api/mcp/servers").json()["canva"]["connected"])
        after = {e["name"]: e for e in client.get("/api/mcp/catalog").json()}["canva"]
        assert after["tools"] == ["whoami"] and after["tools_from"] == "service"

        client.delete("/api/mcp/servers/canva")
        kept = {e["name"]: e for e in client.get("/api/mcp/catalog").json()}["canva"]
        assert kept["tools"] == ["whoami"]


def test_post_mcp_server_persists_config_and_sets_env_var_when_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same real-os.environ-pollution risk as test_web.py's identical test --
    # see that test's comment for the full explanation. create_app_lg's own
    # load_dotenv(".env") call is the culprit here too.
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/mcp/servers",
            json={"name": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
        )

        assert response.status_code == 200
        # connect_one_mcp_server_lg is stubbed to [] by _client_lg -- "saved
        # but didn't connect live" -- see the dedicated splice-in test below
        # for the case where it actually returns tools.
        assert response.json() == {"rejected": {}, "connected": False, "error": None}

        mcp_config = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
        assert mcp_config["mcpServers"]["fetch"]["command"] == "uvx"
        assert dotenv_values(tmp_path / ".env")["COSCRIBE_MCP_CONFIG_PATH"] == str(
            tmp_path / "mcp.json"
        )

        servers = client.get("/api/mcp/servers").json()
        assert servers == {
            "fetch": {
                "command": "uvx",
                "args": ["mcp-server-fetch"],
                "masked_env": {},
                "connected": False,
                "secret_error": None,
                "tools": [],
            }
        }


def test_post_mcp_server_reconnect_retries_a_previously_failed_add(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real gap: once a server is added (`isAdded`
    true), the Connectors panel had no way to replay a failed connect --
    only Remove + re-add, which also throws away the saved config.
    /reconnect re-runs connect_one_mcp_server_lg against the *same*
    persisted config, so a connector that failed once (e.g. a timeout,
    a stale npm cache) can be retried without deleting it first."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        # connect_one_mcp_server_lg is stubbed to [] by _client_lg -- saved
        # but not connected, same as the first attempt failing.
        add_response = client.post(
            "/api/mcp/servers",
            json={"name": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
        )
        assert add_response.json() == {"rejected": {}, "connected": False, "error": None}

        # Simulates the underlying problem being resolved before the retry
        # (e.g. the user installed uv) -- this time connect succeeds.
        class _FakeConnection:
            async def close(self) -> None:
                pass

        async def _fake_connect_succeeds(
            name: str, config: Any, *args: Any, **kwargs: Any
        ) -> tuple[list[Any], Any, None]:
            from coscribe.runtime.types import tool_metadata

            def _tool(x: str = "") -> str:
                """fake"""
                return x

            _tool.__name__ = "fetch__tool"
            tool_metadata(_tool, risk_category="READ", category="mcp:fetch")
            return [_tool], _FakeConnection(), None

        monkeypatch.setattr(
            "coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", _fake_connect_succeeds
        )

        reconnect_response = client.post("/api/mcp/servers/fetch/reconnect")
        assert reconnect_response.json() == {"connected": True, "error": None}

        servers = client.get("/api/mcp/servers").json()
        assert servers["fetch"]["connected"] is True
        # The persisted config itself is untouched by a reconnect -- only
        # bump-version is allowed to rewrite mcp.json.
        mcp_config = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
        assert mcp_config["mcpServers"]["fetch"]["command"] == "uvx"


def test_post_mcp_server_reconnect_reports_not_found_for_an_unconfigured_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post("/api/mcp/servers/does-not-exist/reconnect")
        assert response.json() == {"error": "not found", "connected": False}


def test_get_mcp_servers_connected_reflects_a_real_live_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """connected must be a live signal (web/app.py's mcp_connections
    registry), not just "is it in mcp.json" -- add one server that stubs a
    successful connect and one that doesn't, and check GET /api/mcp/servers
    tells them apart."""

    class _FakeConnection:
        async def close(self) -> None:
            pass

    async def _fake_connect_returns_a_tool(
        name: str, config: Any, *args: Any, **kwargs: Any
    ) -> tuple[list[Any], Any, None]:
        from coscribe.runtime.types import tool_metadata

        def _tool(x: str = "") -> str:
            """fake"""
            return x

        _tool.__name__ = f"{name}__tool"
        tool_metadata(_tool, risk_category="READ", category=f"mcp:{name}")
        return [_tool], _FakeConnection(), None

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        # Stubbed to [] by _client_lg's own default -- never actually connects.
        client.post("/api/mcp/servers", json={"name": "flaky", "command": "uvx", "args": ["x"]})

        monkeypatch.setattr(
            "coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", _fake_connect_returns_a_tool
        )
        client.post("/api/mcp/servers", json={"name": "healthy", "command": "uvx", "args": ["y"]})

        servers = client.get("/api/mcp/servers").json()

    assert servers["flaky"]["connected"] is False
    assert servers["healthy"]["connected"] is True


def test_post_mcp_server_accepts_a_remote_server_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Settings > Connectors > Add > Add manually > Remote -- the frontend
    # form this backs was new work, not just a UI reskin: validate_mcp_
    # config (tools/mcp.py) already handled server_url/headers, but
    # POST /api/mcp/servers's own MCPServerUpdate model only ever accepted
    # command/args/env until now.
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/mcp/servers",
            json={
                "name": "remote-thing",
                "server_url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer token"},
            },
        )
        assert response.status_code == 200
        assert response.json()["rejected"] == {}

        mcp_config = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
        assert mcp_config["mcpServers"]["remote-thing"]["server_url"] == "https://example.com/mcp"

        servers = client.get("/api/mcp/servers").json()
        assert servers["remote-thing"]["server_url"] == "https://example.com/mcp"
        assert servers["remote-thing"]["masked_headers"]["Authorization"].endswith("oken")

        client.delete("/api/mcp/servers/remote-thing")


def test_post_mcp_server_stores_headers_via_keyring_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same guarantee as the identical env-var test above, for a remote
    # server's headers instead of a local server's env.
    fake_keyring = _install_fake_keyring(monkeypatch)
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        client.post(
            "/api/mcp/servers",
            json={
                "name": "remote-secret",
                "server_url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer real-secret"},
            },
        )

        raw = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
        assert raw["mcpServers"]["remote-secret"]["headers"] == {
            "Authorization": {"keyring_ref": "mcp:remote-secret:headers:Authorization"}
        }
        assert (
            fake_keyring.store[("coscribe", "mcp:remote-secret:headers:Authorization")]
            == "Bearer real-secret"
        )
        assert "real-secret" not in json.dumps(raw)

        client.delete("/api/mcp/servers/remote-secret")

    assert ("coscribe", "mcp:remote-secret:headers:Authorization") not in fake_keyring.store


def test_post_mcp_server_rejects_a_server_url_without_https(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.post(
            "/api/mcp/servers",
            json={"name": "bad-remote", "server_url": "ftp://example.com"},
        )

    assert response.status_code == 200
    assert "bad-remote" in response.json()["rejected"]


def test_lifespan_backgrounds_a_slow_mcp_connect_instead_of_blocking_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, user-reported bug: lifespan() used to
    `await connect_mcp_tools_lg(...)` directly, so the whole app -- not
    just one connector's own tools -- didn't start serving *anything*
    until every configured MCP server finished connecting. A slow one (a
    real Playwright launch is the worst case) meant a repeatable
    multi-minute wait on every single cold start, every time, regardless
    of which desktop shell was used. Fixed by bounding that wait
    (MCP_STARTUP_TIMEOUT_SECONDS) and letting a still-connecting server
    finish in the background instead, splicing its tools in once ready --
    the exact mechanism a live mid-conversation connector-add already
    uses (see test_post_mcp_server_splices_tools_into_both_new_and_
    already_open_sessions right below). Proves both halves: a session
    opens while the slow connect is still unfinished, and that same session
    picks up the tool once the background connect completes, with no
    reconnect needed."""
    from coscribe.runtime.types import tool_metadata

    def _fake_tool_fn(x: str = "") -> str:
        """A fake MCP tool for this test."""
        return f"fetched:{x}"

    _fake_tool_fn.__name__ = "fetch__fetch_url"
    tool_metadata(_fake_tool_fn, risk_category="READ", category="mcp:fetch")

    class _FakeConnection:
        async def close(self) -> None:
            pass

    # The connect finishes only when the test releases it, so "the session
    # opened before the connect finished" is a fact to check, not a race
    # against how fast this machine opens a session.
    release = threading.Event()
    finished = threading.Event()

    async def _slow_connect_mcp_tools_lg(
        config_path: Path, *args: Any, **kwargs: Any
    ) -> tuple[list[Any], dict[str, Any]]:
        await asyncio.to_thread(release.wait, 10)
        finished.set()
        return [_fake_tool_fn], {"fetch": _FakeConnection()}

    monkeypatch.setattr("coscribe.web.app.MCP_STARTUP_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("coscribe.runtime_lg.mcp.connect_mcp_tools_lg", _slow_connect_mcp_tools_lg)

    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps({"mcpServers": {"fetch": {"command": "uvx", "args": ["mcp-server-fetch"]}}}),
        encoding="utf-8",
    )
    call = _tool_call("call_1", "fetch__fetch_url", {"x": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model, mcp_config_path=config_path) as client:
        with client.websocket_connect("/ws/t_startup_bg") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
        assert not finished.is_set()

        release.set()
        assert finished.wait(10)
        # Splicing the tools into open sessions follows right after.
        time.sleep(0.5)

        with client.websocket_connect("/ws/t_startup_bg") as ws:
            state = ws.receive_json()
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "fetch hi"})
            messages = _receive_until(ws, "tasks_changed")

    tool_result = next(m for m in messages if m["type"] == "tool_result")
    assert tool_result["tool_name"] == "fetch__fetch_url"
    assert tool_result["result"] == "fetched:hi"
    # The page counts a connector's calls as "used N tools" by this list.
    assert state["connector_tools"] == ["fetch__fetch_url"]


def test_post_mcp_server_splices_tools_into_both_new_and_already_open_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for a real, live-reported bug: a user enabled a
    connector mid-conversation and it had no effect on that same,
    already-open thread -- only a brand-new thread picked it up (matching
    what used to be the deliberate, documented gap in app.py's module
    docstring). Fixed via ChatSessionLG.refresh_extra_tools +
    _refresh_all_sessions_extra_tools, called after add/remove/bump-version
    below. This drives the "before" thread's ChatSessionLG into existence
    *before* the connector is added, then proves its *same* thread_id
    picks up the freshly-spliced tool on its very next message -- not just
    that a brand-new "after" thread does (also checked, same as before).
    Behavioral proof both times, not just a config-file check: has each
    session's model actually call the tool and confirms it executes (a
    real BaseTool the compiled graph genuinely knows about), not just that
    /api/mcp/servers reflects it."""
    from coscribe.runtime.types import tool_metadata

    def _fake_tool_fn(x: str = "") -> str:
        """A fake MCP tool for this test."""
        return f"fetched:{x}"

    _fake_tool_fn.__name__ = "fetch__fetch_url"
    tool_metadata(_fake_tool_fn, risk_category="READ", category="mcp:fetch")

    class _FakeConnection:
        async def close(self) -> None:
            pass

    async def _fake_connect_returns_a_tool(
        name: str, config: Any, *args: Any, **kwargs: Any
    ) -> tuple[list[Any], Any, None]:
        return [_fake_tool_fn], _FakeConnection(), None

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    call = _tool_call("call_1", "fetch__fetch_url", {"x": "hi"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
            AIMessage(content="", tool_calls=[call]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        # Opened *before* the connector is added -- forces ChatSessionLG's
        # thread-a graph to be built with no "fetch" tool at all, so
        # picking it up below can only be explained by refresh_extra_tools,
        # not by the tool having been there all along.
        with client.websocket_connect("/ws/before") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history

        # Must be set *after* entering _client_lg -- its own body sets a
        # default stub for this same name on entry, which would otherwise
        # overwrite this one (bit me once already; see the surrounding
        # comment in _client_lg for why the default exists at all).
        monkeypatch.setattr(
            "coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", _fake_connect_returns_a_tool
        )
        response = client.post(
            "/api/mcp/servers",
            json={"name": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
        )
        assert response.json() == {"rejected": {}, "connected": True, "error": None}

        with client.websocket_connect("/ws/before") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "fetch hi"})
            before_messages = _receive_until(ws, "tasks_changed")

        with client.websocket_connect("/ws/after") as ws:
            ws.receive_json()  # state
            ws.receive_json()  # history
            ws.send_json({"type": "user_message", "text": "fetch hi"})
            after_messages = _receive_until(ws, "tasks_changed")

    for messages in (before_messages, after_messages):
        tool_result = next(m for m in messages if m["type"] == "tool_result")
        assert tool_result["tool_name"] == "fetch__fetch_url"
        assert tool_result["result"] == "fetched:hi"


def test_delete_mcp_server_removes_only_that_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "fetch": {"command": "uvx", "args": ["mcp-server-fetch"]},
                    "fs": {"command": "npx", "args": ["fs-server"]},
                }
            }
        ),
        encoding="utf-8",
    )
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model, mcp_config_path=config_path) as client:
        response = client.delete("/api/mcp/servers/fetch")

    assert response.status_code == 200
    remaining = json.loads(config_path.read_text(encoding="utf-8"))
    assert set(remaining["mcpServers"]) == {"fs"}


def test_get_providers_catalog_returns_curated_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        response = client.get("/api/providers/catalog")

    assert response.status_code == 200
    entries = {entry["name"]: entry for entry in response.json()}
    assert entries["anthropic"]["builtin"] is True
    assert entries["deepseek"]["builtin"] is False


def test_providers_catalog_includes_a_local_ollama_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """coscribe's whole pitch is local-first, but until now the provider
    catalog only offered cloud presets (deepseek/kimi/glm) -- no one-click
    entry for a fully offline model runtime. Ollama's own OpenAI-compatible
    server never checks the Authorization header, so unlike every other
    catalog entry its api_key is a placeholder, not a real secret; proves
    the round trip still works end to end with one anyway, since
    add_provider (unconditionally) and the frontend form both still
    require a non-blank value regardless of whether the provider itself
    cares."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        catalog = {entry["name"]: entry for entry in client.get("/api/providers/catalog").json()}
        assert catalog["ollama"]["builtin"] is False
        assert catalog["ollama"]["base_url"] == "http://localhost:11434/v1"

        response = client.post(
            "/api/providers",
            json={
                "name": "ollama",
                "base_url": catalog["ollama"]["base_url"],
                "api_key": "ollama",
                "default_model": "qwen2.5",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"restart_required": False, "rejected": {}}

        providers = client.get("/api/providers").json()
        assert providers["ollama"]["base_url"] == "http://localhost:11434/v1"
        assert providers["ollama"]["default_model"] == "qwen2.5"


def test_post_mcp_server_stores_env_via_keyring_when_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_keyring = _install_fake_keyring(monkeypatch)
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        client.post(
            "/api/mcp/servers",
            json={
                "name": "github-local",
                "command": "docker",
                "args": ["run", "github-mcp-server"],
                "env": {"GITHUB_TOKEN": "ghp_real_token"},
            },
        )

        raw = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
        assert raw["mcpServers"]["github-local"]["env"] == {
            "GITHUB_TOKEN": {"keyring_ref": "mcp:github-local:env:GITHUB_TOKEN"}
        }
        assert (
            fake_keyring.store[("coscribe", "mcp:github-local:env:GITHUB_TOKEN")]
            == "ghp_real_token"
        )

        servers = client.get("/api/mcp/servers").json()
        assert servers["github-local"]["masked_env"]["GITHUB_TOKEN"].endswith("ken")

        client.delete("/api/mcp/servers/github-local")

    assert ("coscribe", "mcp:github-local:env:GITHUB_TOKEN") not in fake_keyring.store


def test_mcp_json_file_gets_owner_only_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        client.post(
            "/api/mcp/servers",
            json={"name": "fetch", "command": "uvx", "args": ["mcp-server-fetch"]},
        )

    assert stat.S_IMODE((tmp_path / "mcp.json").stat().st_mode) == 0o600


def test_skill_plugins_can_be_previewed_then_added_with_connectors_matched_to_ours(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.tools import skill_catalog

    from ..test_skill_catalog import _plugin_catalog

    catalog, served = _plugin_catalog()
    catalog["plugins"][0].update(
        title="Pl",
        author="A",
        version="1.0",
        description="d",
        license="Apache-2.0",
        updated="2026-01-01",
        connectors=[
            {"name": "notion", "url": "https://mcp.notion.com/mcp/"},
            {"name": "odd", "url": "https://example.invalid/mcp"},
        ],
    )
    monkeypatch.setattr(skill_catalog, "load_catalog", lambda: catalog)
    monkeypatch.setattr("coscribe.web.routes.skills.load_catalog", lambda: catalog)
    monkeypatch.setattr(skill_catalog, "_download", served.__getitem__)
    skill_catalog._preview_cache.clear()
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        listed = client.get("/api/skills/plugins").json()
        detail = client.get("/api/skills/plugins/pl").json()
        preview = client.get("/api/skills/plugins/pl/files/skills/one/SKILL.md")
        outside = client.get("/api/skills/plugins/pl/files/../x")
        unknown = client.get("/api/skills/plugins/nope")
        added = client.post("/api/skills/plugins/pl")
        after = client.get("/api/skills/plugins").json()

    assert [(p["id"], p["added"]) for p in listed] == [("pl", 0)]
    assert [c["connector"] for c in detail["connectors"]] == ["notion", None]
    assert detail["files"] == ["skills/one/SKILL.md", "skills/two/SKILL.md"]
    assert preview.json()["content"].endswith("one")
    assert outside.status_code == 404 and unknown.status_code == 404
    assert added.json() == {"added": ["one", "two"]}
    assert after[0]["added"] == 2


def test_connector_tool_permissions_change_what_a_conversation_may_do_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from langchain_core.tools import StructuredTool

    from coscribe.runtime.types import tool_metadata

    def _make(name: str, read_only: bool) -> Any:
        def run(x: str = "") -> str:
            return f"{name}:{x}"

        tool = StructuredTool.from_function(
            run, name=name, description=f"{name}. More.", metadata={"readOnlyHint": read_only}
        )
        tool_metadata(tool, risk_category="EXTERNAL", category="mcp:notes")
        return tool

    tools = [_make("notes_read", True), _make("notes_delete", False)]

    class _FakeConnection:
        async def close(self) -> None:
            pass

    async def _connect(
        name: str, config: Any, *args: Any, **kwargs: Any
    ) -> tuple[list[Any], Any, None]:
        return tools, _FakeConnection(), None

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    read = _tool_call("r1", "notes_read", {"x": "a"})
    fake_model = FakeToolCallingChatModel(
        responses=[
            AIMessage(content="", tool_calls=[read]),
            AIMessage(content="done"),
            AIMessage(content="", tool_calls=[read]),
            AIMessage(content="done"),
        ]
    )
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        monkeypatch.setattr("coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", _connect)
        client.post("/api/mcp/servers", json={"name": "notes", "command": "npx", "args": ["n"]})

        listed = client.get("/api/mcp/servers").json()["notes"]["tools"]
        with client.websocket_connect("/ws/t_perm_ask") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "user_message", "text": "read"})
            asked = _receive_until(ws, "approval_required")[-1]
            ws.send_json({"type": "approval_response", "id": asked["id"], "approved": True})
            _receive_until(ws, "tasks_changed")

        saved = client.put(
            "/api/mcp/servers/notes/permissions",
            json={"tools": {"notes_read": "allow", "notes_delete": "block"}},
        )
        with client.websocket_connect("/ws/t_perm_ask") as ws:
            ws.receive_json()
            ws.receive_json()
            ws.send_json({"type": "user_message", "text": "read again"})
            allowed = _receive_until(ws, "tasks_changed")
        tool_names = [t["name"] for t in client.get("/api/tools").json()["tools"]]
        refused = client.put(
            "/api/mcp/servers/notes/permissions", json={"tools": {"notes_read": "never"}}
        )

    assert [(t["name"], t["title"], t["read_only"], t["policy"]) for t in listed] == [
        ("notes_read", "Read", True, "ask"),
        ("notes_delete", "Delete", False, "ask"),
    ]
    assert asked["tool_name"] == "notes_read"
    assert saved.json() == {"tools": {"notes_read": "allow", "notes_delete": "block"}}
    assert not any(m["type"] == "approval_required" for m in allowed)
    assert next(m for m in allowed if m["type"] == "tool_result")["result"] == "notes_read:a"
    assert "notes_read" in tool_names and "notes_delete" not in tool_names
    assert refused.status_code == 422


# -- Connectors that sign in through the browser -------------------------------


def _sign_in_like_a_browser(client: Any, opened: list[str], approve: bool = True) -> Any:
    """What webbrowser.open does in a test: follow the provider's sign-in
    page, which redirects back to coscribe, and visit that address as the
    browser would."""
    from urllib.parse import parse_qs, urlparse

    import httpx

    def open_page(url: str) -> None:
        opened.append(url)

        def visit() -> None:
            location = httpx.get(url, follow_redirects=False).headers["location"]
            if not approve:
                state = parse_qs(urlparse(location).query)["state"][0]
                location = f"{location.split('?')[0]}?error=access_denied&state={state}"
            if location.startswith("http://localhost:"):
                httpx.get(location)
            else:
                client.get(location.replace("http://testserver", ""))

        threading.Thread(target=visit, daemon=True).start()

    return open_page


def _wait_until(condition: Any, seconds: float = 20.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.1)


@contextlib.contextmanager
def _oauth_connector_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_tool: bool = True
) -> Any:
    """A running app, a running OAuth MCP server, and a "browser" that
    approves every sign-in; yields (client, server, opened pages)."""
    from coscribe.runtime_lg import mcp as lg_mcp
    from tests.oauth_mcp_server import running_oauth_mcp_server

    real_connect = lg_mcp.connect_one_mcp_server_lg
    monkeypatch.delenv("COSCRIBE_MCP_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    with (
        running_oauth_mcp_server(with_tool) as server,
        _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client,
    ):
        monkeypatch.setattr("coscribe.runtime_lg.mcp.connect_one_mcp_server_lg", real_connect)
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", _sign_in_like_a_browser(client, opened))
        yield client, server, opened


def _add_oauth_connector(client: Any, server: Any) -> dict[str, Any]:
    response = client.post(
        "/api/mcp/servers",
        json={"name": "docs", "server_url": server.mcp_url, "auth": "oauth"},
    )
    assert response.status_code == 200
    result: dict[str, Any] = response.json()
    return result


def test_an_oauth_connector_signs_in_through_the_browser_and_keeps_the_sign_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _oauth_connector_client(tmp_path, monkeypatch) as (client, server, opened):
        result = _add_oauth_connector(client, server)

        assert result["connected"] is False
        assert result["signin"]["url"].startswith(f"{server.url}/authorize?")
        assert opened == [result["signin"]["url"]]
        _wait_until(lambda: client.get("/api/mcp/servers").json()["docs"]["connected"])

        info = client.get("/api/mcp/servers").json()["docs"]
        assert info["auth"] == "oauth" and info["signin"] is None
        assert [t["name"] for t in info["tools"]] == ["docs_whoami"]
        # No secret in the connector list, and only the choice of OAuth in mcp.json.
        assert json.loads((tmp_path / "mcp.json").read_text())["mcpServers"]["docs"] == {
            "server_url": server.mcp_url,
            "auth": "oauth",
        }
        assert server.provider.registrations == 1

        # A restart: the saved sign-in is used, no browser opens.
        reconnected = client.post("/api/mcp/servers/docs/reconnect").json()
        assert reconnected["connected"] is True and len(opened) == 1
        assert server.provider.registrations == 1


def _register_app(server: Any, client_id: str, secret: str) -> None:
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    from coscribe.runtime_lg.mcp_oauth import APP_REDIRECT_URI

    server.provider.clients[client_id] = OAuthClientInformationFull(
        client_id=client_id,
        client_secret=secret,
        redirect_uris=[AnyUrl(APP_REDIRECT_URI)],
        token_endpoint_auth_method="client_secret_post",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
    )


def test_a_connector_with_its_own_registered_app_signs_in_on_the_fixed_port_without_registering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from coscribe.runtime_lg.mcp_oauth import APP_REDIRECT_URI

    with _oauth_connector_client(tmp_path, monkeypatch) as (client, server, opened):
        _register_app(server, "my-app-id", "my-app-secret")
        refused = client.put("/api/mcp/oauth-apps/acme", json={"client_id": "has space"})
        saved = client.put(
            "/api/mcp/oauth-apps/acme",
            json={"client_id": "my-app-id", "client_secret": "my-app-secret"},
        )
        response = client.post(
            "/api/mcp/servers",
            json={
                "name": "docs",
                "server_url": server.mcp_url,
                "auth": "oauth",
                "oauth_app": "acme",
            },
        )
        _wait_until(lambda: client.get("/api/mcp/servers").json()["docs"]["connected"])
        listed = client.get("/api/mcp/oauth-apps").json()
        exported = client.get("/api/mcp/oauth-apps/acme/export").json()
        client.delete("/api/mcp/oauth-apps/acme")
        after = client.get("/api/mcp/oauth-apps").json()

    assert refused.status_code == 400 and saved.json() == {"saved": "acme"}
    assert response.json()["signin"]["url"].startswith(f"{server.url}/authorize?")
    assert "client_id=my-app-id" in opened[0]
    assert f"redirect_uri={APP_REDIRECT_URI.replace(':', '%3A').replace('/', '%2F')}" in opened[0]
    assert server.provider.registrations == 0
    assert listed == {"saved": ["acme"], "redirect_uri": APP_REDIRECT_URI}
    assert exported == {
        "apps": {"acme": {"client_id": "my-app-id", "client_secret": "my-app-secret"}}
    }
    assert after["saved"] == []


def test_a_connector_that_needs_its_app_says_so_and_a_busy_port_is_explained(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    from coscribe.runtime_lg.mcp_oauth import CALLBACK_PORT

    with _oauth_connector_client(tmp_path, monkeypatch) as (client, server, opened):
        body = {"name": "docs", "server_url": server.mcp_url, "auth": "oauth", "oauth_app": "acme"}
        missing = client.post("/api/mcp/servers", json=body).json()
        client.put("/api/mcp/oauth-apps/acme", json={"client_id": "x", "client_secret": "y"})
        with socket.socket() as squatter:
            squatter.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            squatter.bind(("127.0.0.1", CALLBACK_PORT))
            squatter.listen()
            busy = client.post("/api/mcp/servers", json=body).json()

    assert missing["connected"] is False and "Set up the app" in missing["error"]
    assert busy["connected"] is False and str(CALLBACK_PORT) in busy["error"]
    assert opened == []


def test_imported_app_setups_are_saved_for_the_services_they_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _oauth_connector_client(tmp_path, monkeypatch) as (client, _server, _opened):
        bad = client.post("/api/mcp/oauth-apps/import", json={"apps": {"g": {"client_id": " "}}})
        good = client.post(
            "/api/mcp/oauth-apps/import",
            json={"apps": {"google": {"client_id": "a", "client_secret": "b"}}},
        )
        listed = client.get("/api/mcp/oauth-apps").json()

    assert bad.status_code == 400
    assert good.json() == {"imported": ["google"]}
    assert listed["saved"] == ["google"]


def test_an_expired_oauth_sign_in_is_refreshed_without_the_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _oauth_connector_client(tmp_path, monkeypatch) as (client, server, opened):
        server.provider.token_lifetime = 1
        _add_oauth_connector(client, server)
        _wait_until(lambda: client.get("/api/mcp/servers").json()["docs"]["connected"])
        time.sleep(2)

        assert client.post("/api/mcp/servers/docs/reconnect").json()["connected"] is True
        assert len(opened) == 1


def test_a_refused_oauth_sign_in_says_so_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _oauth_connector_client(tmp_path, monkeypatch) as (client, server, opened):
        monkeypatch.setattr("webbrowser.open", _sign_in_like_a_browser(client, opened, False))
        _add_oauth_connector(client, server)
        _wait_until(lambda: client.get("/api/mcp/servers").json()["docs"]["signin_error"])

        info = client.get("/api/mcp/servers").json()["docs"]
        assert info["connected"] is False and info["signin"] is None
        assert "access_denied" in info["signin_error"]

        monkeypatch.setattr("webbrowser.open", _sign_in_like_a_browser(client, opened))
        assert client.post("/api/mcp/servers/docs/signin").json()["signin"]["url"]
        _wait_until(lambda: client.get("/api/mcp/servers").json()["docs"]["connected"])
        assert client.get("/api/mcp/servers").json()["docs"]["signin_error"] is None


def test_reconnecting_without_a_saved_sign_in_asks_for_one_instead_of_opening_a_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _oauth_connector_client(tmp_path, monkeypatch) as (client, server, opened):
        _add_oauth_connector(client, server)
        _wait_until(lambda: client.get("/api/mcp/servers").json()["docs"]["connected"])
        (tmp_path / "state" / "mcp_oauth" / "docs.json").unlink()

        result = client.post("/api/mcp/servers/docs/reconnect").json()

        assert result["connected"] is False and "sign in" in result["error"].lower()
        assert len(opened) == 1


def test_removing_an_oauth_connector_forgets_its_sign_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _oauth_connector_client(tmp_path, monkeypatch) as (client, server, _opened):
        _add_oauth_connector(client, server)
        _wait_until(lambda: client.get("/api/mcp/servers").json()["docs"]["connected"])
        assert (tmp_path / "state" / "mcp_oauth" / "docs.json").is_file()

        client.delete("/api/mcp/servers/docs")

        assert not (tmp_path / "state" / "mcp_oauth" / "docs.json").exists()


def test_an_oauth_redirect_nobody_is_waiting_for_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        response = client.get("/api/mcp/oauth/callback?state=nope&code=x")

        assert response.status_code == 400
        assert "expired" in response.text


def test_signing_in_again_while_a_sign_in_waits_replaces_it_cleanly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _oauth_connector_client(tmp_path, monkeypatch) as (client, server, opened):
        ignored: list[str] = []
        monkeypatch.setattr("webbrowser.open", ignored.append)
        _add_oauth_connector(client, server)
        assert client.get("/api/mcp/servers").json()["docs"]["signin"] is not None

        monkeypatch.setattr("webbrowser.open", _sign_in_like_a_browser(client, opened))
        assert client.post("/api/mcp/servers/docs/signin").json()["signin"]["url"]

        _wait_until(lambda: client.get("/api/mcp/servers").json()["docs"]["connected"])
        info = client.get("/api/mcp/servers").json()["docs"]
        assert info["signin"] is None and info["signin_error"] is None


def test_signing_in_to_a_server_with_no_tools_says_so_instead_of_pretending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _oauth_connector_client(tmp_path, monkeypatch, with_tool=False) as (client, server, _):
        _add_oauth_connector(client, server)
        _wait_until(lambda: client.get("/api/mcp/servers").json()["docs"]["signin_error"])

        info = client.get("/api/mcp/servers").json()["docs"]
        assert info["connected"] is False
        assert info["signin_error"] == "The server connected but offers no tools."

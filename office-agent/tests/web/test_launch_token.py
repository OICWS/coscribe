"""A page on another loopback port cannot drive the server: each launch makes a token, and a
WebSocket handshake or a request that changes state must carry it (web/local_guard.py)."""

from pathlib import Path

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from coscribe.web.local_guard import ALLOWED_HOSTS_ENV, TOKEN_ENV, LocalOnlyGuard, launch_token

from ..conftest import LAUNCH_TOKEN
from .helpers import FakeToolCallingChatModel, _client_lg

TOKEN = "t0ken"
HEADER = {"x-coscribe-token": TOKEN}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv(ALLOWED_HOSTS_ENV, raising=False)
    app = FastAPI()
    app.add_middleware(LocalOnlyGuard, token=TOKEN)

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/act")
    def act() -> dict[str, bool]:
        return {"done": True}

    @app.post("/internal/browser-host/result")
    def own_token() -> dict[str, bool]:
        return {"done": True}

    @app.get("/api/mcp/oauth/callback")
    def callback() -> dict[str, bool]:
        return {"signed_in": True}

    @app.websocket("/ws")
    async def ws(socket: WebSocket) -> None:
        await socket.accept()
        await socket.send_text("hello")
        await socket.close()

    return TestClient(app, base_url="http://127.0.0.1:8000")


def test_a_request_that_changes_state_needs_the_token(client: TestClient) -> None:
    assert client.post("/act").status_code == 403
    assert client.post("/act", headers={"x-coscribe-token": "guess"}).status_code == 403
    assert client.post("/act", headers=HEADER).status_code == 200
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)("/act").status_code == 403


def test_the_refusal_says_why(client: TestClient) -> None:
    assert "token" in client.post("/act").json()["detail"].lower()


def test_a_read_needs_no_token(client: TestClient) -> None:
    """An image, a download link and an iframe cannot send headers; reads change nothing (the audit
    below) and a page on another port cannot read the answer."""
    assert client.get("/ping").status_code == 200


def test_the_browser_host_routes_keep_their_own_token(client: TestClient) -> None:
    assert client.post("/internal/browser-host/result").status_code == 200


def test_a_websocket_needs_the_token_in_the_query_or_a_header(client: TestClient) -> None:
    local = {"Host": "127.0.0.1:8000"}
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/ws", headers=local):
            pass
    assert refused.value.code == 1008
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws?token=guess", headers=local):
            pass
    with client.websocket_connect(f"/ws?token={TOKEN}", headers=local) as ws:
        assert ws.receive_text() == "hello"
    with client.websocket_connect("/ws", headers={**local, **HEADER}) as ws:
        assert ws.receive_text() == "hello"


def test_the_oauth_callback_may_arrive_from_the_identity_provider(client: TestClient) -> None:
    """A redirect from another site is a cross-site navigation by nature; every other route
    still refuses one."""
    cross = {"Sec-Fetch-Site": "cross-site"}
    assert client.get("/api/mcp/oauth/callback", headers=cross).status_code == 200
    assert client.get("/ping", headers=cross).status_code == 403
    assert client.post("/api/mcp/oauth/callback", headers={**cross, **HEADER}).status_code in (
        403,
        405,
    )


def test_no_token_configured_means_no_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOWED_HOSTS_ENV, raising=False)
    app = FastAPI()
    app.add_middleware(LocalOnlyGuard)

    @app.post("/act")
    def act() -> dict[str, bool]:
        return {"done": True}

    assert TestClient(app, base_url="http://127.0.0.1:8000").post("/act").status_code == 200


def test_each_launch_makes_its_own_token_unless_one_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    assert launch_token() != launch_token()
    assert len(launch_token()) >= 40
    monkeypatch.setenv(TOKEN_ENV, "fixed")
    assert launch_token() == "fixed"


def test_the_real_app_hands_its_page_the_token_and_refuses_a_post_without_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = Path(__file__).parents[2] / "src" / "coscribe" / "web" / "static"
    made_static = not static.exists()
    static.mkdir(exist_ok=True)
    index = static / "index.html"
    wrote_index = not index.exists()
    if wrote_index:
        index.write_text("<html><head></head><body></body></html>", encoding="utf-8")
    try:
        with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
            page = client.get("/")
            assert f'<meta name="coscribe-token" content="{LAUNCH_TOKEN}">' in page.text
            assert page.headers["cache-control"] == "no-store"
            bare = TestClient(client.app, base_url="http://127.0.0.1:8000")
            local = {"Host": "127.0.0.1:8000"}
            assert bare.post("/api/threads/x/messaging", json={}, headers=local).status_code == 403
            with pytest.raises(WebSocketDisconnect) as refused:
                with bare.websocket_connect("/ws/some-thread", headers=local):
                    pass
            assert refused.value.code == 1008
    finally:
        if wrote_index:
            index.unlink()
        if made_static:
            static.rmdir()


# Every GET route, read by a person for a side effect. A GET carries no token, and only the
# `Sec-Fetch-Site` check keeps another page on this machine from sending one (a browser that
# sends no such header is not covered), so a GET must not change anything that matters: a new
# GET route fails this test until someone reads it and adds it here.
AUDITED_READ_ONLY_GETS = {
    "/",
    "/api/artifacts",
    "/api/artifacts/lookup",
    "/api/artifacts/{artifact}/content",
    "/api/threads/{thread_id}/design",
    "/api/threads/{thread_id}/docs/blocks",
    "/api/threads/{thread_id}/docs/image",
    "/api/artifacts/{artifact}/thumbnail",
    "/api/attachment/pdf",
    "/api/attachment/pdf/page",
    "/api/background-tasks/{task_id}/log",
    "/api/browse-dirs",
    "/api/capabilities",
    "/api/code",
    "/api/environments",
    "/api/code/permissions",
    "/api/command-sandbox",
    "/api/commands",
    "/api/config",
    "/api/design-systems",
    "/api/design-systems/{system_id}",
    "/api/mcp/catalog",
    "/api/mcp/npm-latest-version",
    "/api/mcp/oauth-apps",
    "/api/mcp/oauth-apps/{group}/export",
    "/api/mcp/oauth/callback",
    "/api/mcp/servers",
    "/api/memory",
    "/api/node-env/packages",
    "/api/pptx-shapes",
    "/api/previews/{name}",
    "/api/providers",
    "/api/providers/catalog",
    "/api/screenshots/{name}",
    "/api/script-env/interpreter",
    "/api/script-env/packages",
    "/api/scheduled-tasks",
    "/api/scheduled-tasks/{trigger_id}/notes",
    "/api/secrets",
    "/api/skills",
    "/api/skills/catalog",
    "/api/skills/plugins",
    "/api/script-isolation",
    "/api/skills/plugins/{plugin_id}",
    "/api/skills/plugins/{plugin_id}/files/{path}",
    "/api/skills/{name}/files",
    "/api/skills/{name}/files/{path}",
    "/api/subagents/{task_id}/transcript",
    "/api/thread-groups",
    "/api/threads",
    "/api/threads/status",
    "/api/threads/{thread_id}/activity",
    "/api/threads/{thread_id}/background-tasks",
    "/api/threads/{thread_id}/context-breakdown",
    "/api/threads/{thread_id}/docx-review",
    "/api/threads/{thread_id}/docx-review/original",
    "/api/threads/{thread_id}/files/download",
    "/api/threads/{thread_id}/messaging",
    "/api/threads/{thread_id}/slides",
    "/api/threads/{thread_id}/slides/comments",
    "/api/threads/{thread_id}/slides/design-system",
    "/api/threads/{thread_id}/slides/export",
    "/api/threads/{thread_id}/slides/history",
    "/api/threads/{thread_id}/slides/{index}/parts",
    "/api/threads/{thread_id}/slides/{index}/svg",
    "/api/threads/{thread_id}/subagents",
    "/api/threads/{thread_id}/tasks",
    "/api/tools",
    "/internal/browser-host",
    "/internal/events",
}


def test_every_get_route_has_been_read_for_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _client_lg(tmp_path, monkeypatch, FakeToolCallingChatModel(responses=[])) as client:
        schema = client.get("/openapi.json").json()
    gets = {path for path, operations in schema["paths"].items() if "get" in operations}
    assert gets - AUDITED_READ_ONLY_GETS == set(), (
        "A new GET route: read its handler for anything it changes (a file, a setting, a "
        "running process). If it changes something make it a POST; if not, add it here."
    )
    assert AUDITED_READ_ONLY_GETS - gets == set(), "A route listed here no longer exists."


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_a_get_from_another_page_is_refused(client: TestClient, site: str) -> None:
    assert client.get("/ping", headers={"Sec-Fetch-Site": site}).status_code == 403
    assert client.get("/ping", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.get("/ping", headers={"Sec-Fetch-Site": "none"}).status_code == 200


def test_the_browser_host_exemption_is_not_a_bare_prefix(client: TestClient) -> None:
    assert client.post("/internal/browser-hostX").status_code == 403
    assert client.post("/internal/browser-host/result").status_code == 200

# ruff: noqa: E402
"""Web tests: the internal routes, wake polling and appearance settings."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langgraph", reason="needs the langgraph_spike extra installed")

from dotenv import dotenv_values

from ..test_web_routes import _flatten
from .helpers import (
    FakeToolCallingChatModel,
    _client_lg,
)


def test_background_event_bus_fans_out_to_every_subscriber() -> None:
    """Unit-level coverage of background_events.BackgroundEventBus itself,
    independent of FastAPI/asyncio-loop plumbing: two subscribers each get
    their own copy of a published event, and an unsubscribed queue gets
    nothing further."""

    from coscribe.web.background_events import BackgroundEvent, BackgroundEventBus

    async def _run() -> None:
        bus = BackgroundEventBus()
        q1 = bus.subscribe()
        q2 = bus.subscribe()
        bus.publish(
            BackgroundEvent(kind="wake", status="completed", title="research done", thread_id="t1")
        )
        e1 = q1.get_nowait()
        e2 = q2.get_nowait()
        assert (
            e1
            == e2
            == {
                "type": "background_run_completed",
                "kind": "wake",
                "status": "completed",
                "title": "research done",
                "thread_id": "t1",
                "body": "",
            }
        )
        bus.unsubscribe(q1)
        bus.publish(
            BackgroundEvent(kind="wake", status="completed", title="ignored", thread_id="t1")
        )
        assert q1.empty()
        assert q2.get_nowait()["title"] == "ignored"

    asyncio.run(_run())


def test_internal_events_endpoint_is_a_registered_sse_stream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Structural check that /internal/events exists, is GET-only, and
    responds with a text/event-stream StreamingResponse -- the part of
    background_events_stream (web/app.py) that a real HTTP request never
    finishes exercising in this test suite (see the previous test's
    docstring), covered here without actually driving the infinite
    stream: call the route's endpoint function directly and inspect the
    response object, then explicitly close its body iterator so the
    subscription it opened doesn't linger."""
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        route = next(
            r for r in _flatten(client.app.routes) if getattr(r, "path", None) == "/internal/events"
        )
        assert route.methods == {"GET"}

        async def _probe() -> None:
            response = await route.endpoint()
            assert response.media_type == "text/event-stream"
            await response.body_iterator.aclose()

        client.portal.call(_probe)


def test_a_json_body_without_a_json_content_type_is_refused_lg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cross-site page can POST to 127.0.0.1 without a CORS preflight
    only if it sends no JSON Content-Type -- such a body must not be
    parsed, or any website could drive these endpoints."""
    opened: list[Any] = []
    monkeypatch.setattr(
        "coscribe.web.routes.threads.open_in_os", lambda path, *, reveal: opened.append(path)
    )
    (tmp_path / "workspace").mkdir()
    (tmp_path / "workspace" / "report.xlsx").write_bytes(b"x")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        request = client.build_request(
            "POST", "/api/threads/t_csrf/files/open", content=b'{"path": "report.xlsx"}'
        )
        assert "content-type" not in request.headers
        response = client.send(request)

    assert response.status_code == 422
    assert opened == []


def test_browser_host_endpoints_refuse_anyone_without_the_desktop_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    reply = {"id": "1", "ok": True, "result": {}}
    with _client_lg(tmp_path, monkeypatch, fake_model, browser_host_token="s3cret") as client:
        assert client.get("/internal/browser-host").status_code == 403
        wrong = {"x-coscribe-browser-token": "guess"}
        assert client.get("/internal/browser-host", headers=wrong).status_code == 403
        assert client.post("/internal/browser-host/result", json=reply).status_code == 403
        right = {"x-coscribe-browser-token": "s3cret"}
        response = client.post("/internal/browser-host/result", json=reply, headers=right)
        assert response.status_code == 200


def test_browser_host_is_closed_when_not_started_by_the_desktop_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        headers = {"x-coscribe-browser-token": ""}
        assert client.get("/internal/browser-host", headers=headers).status_code == 403


def test_appearance_settings_save_to_env_and_come_back_without_a_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    updates = {
        "COSCRIBE_THEME": "dark",
        "COSCRIBE_INTERFACE_FONT": "dyslexic",
        "COSCRIBE_MOTION": "reduced",
    }
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        body = client.post("/api/config", json={"updates": updates}).json()
        config = client.get("/api/config").json()

    assert body == {"restart_required": False, "rejected": {}}
    assert {key: config[key] for key in updates} == updates


def test_appearance_settings_refuse_values_the_app_does_not_know(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("", encoding="utf-8")
    fake_model = FakeToolCallingChatModel(responses=[])
    with _client_lg(tmp_path, monkeypatch, fake_model) as client:
        body = client.post("/api/config", json={"updates": {"COSCRIBE_THEME": "purple"}}).json()

    assert "COSCRIBE_THEME" in body["rejected"]
    assert "COSCRIBE_THEME" not in dotenv_values(tmp_path / ".env")

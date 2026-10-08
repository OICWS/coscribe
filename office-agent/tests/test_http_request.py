"""http_request and list_secrets (tools/http_request.py), and the blanking of
secret values out of every tool result (runtime_lg/agent.py): a secret is put
in only at the moment of the request, for its own hosts, over https, and the
model never reads it back."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import httpx
import keyring.errors
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from coscribe.runtime import secrets
from coscribe.runtime.secret_store import SecretError, SecretStore, SessionEnvironments, redactor
from coscribe.runtime_lg.agent import _RedactToolResultsMiddleware
from coscribe.tools import http_request as http_module
from coscribe.tools.http_request import build_http_tools

from .test_code_agent import (
    _Model,
    _run,
    _session,
    _Socket,
)

KEY = "sk-live-abcdef123456"


class _FakeKeyring:
    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.errors = keyring.errors

    def set_password(self, service: str, ref: str, value: str) -> None:
        self.store[(service, ref)] = value

    def get_password(self, service: str, ref: str) -> str | None:
        return self.store.get((service, ref))

    def delete_password(self, service: str, ref: str) -> None:
        self.store.pop((service, ref), None)


@pytest.fixture(autouse=True)
def keychain(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    fake = _FakeKeyring()
    monkeypatch.setattr(secrets, "keyring", fake)
    monkeypatch.setattr(secrets, "keychain_backend_usable", lambda: True)
    return fake


class _Server:
    """Stands in for the network: records what was sent, answers with `reply`."""

    def __init__(self) -> None:
        self.sent: list[httpx.Request] = []
        self.reply: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(
            200, text="ok"
        )

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = self
        real = httpx.Client

        def client(**kwargs: Any) -> httpx.Client:
            def handler(request: httpx.Request) -> httpx.Response:
                server.sent.append(request)
                return server.reply(request)

            kwargs.pop("proxy", None)
            return real(transport=httpx.MockTransport(handler), **kwargs)

        monkeypatch.setattr(http_module, "httpx", SimpleNamespace(Client=client))


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> _Server:
    fake = _Server()
    fake.install(monkeypatch)
    return fake


def _tools(tmp_path: Path, *, attach: bool = True) -> dict[str, Callable[..., Any]]:
    store = SecretStore(tmp_path)
    store.save("API_KEY", KEY, ["api.example.com", "*.cdn.example.com"])
    store.save("OTHER_KEY", "other-secret-value", ["other.example.com"])
    if attach:
        SessionEnvironments(tmp_path).set("t1", {}, ["API_KEY"], store.names())
    return {fn.__name__: fn for fn in build_http_tools(tmp_path, "t1")}


def test_the_secret_is_put_in_at_the_request_and_not_returned(
    tmp_path: Path, server: _Server
) -> None:
    tools = _tools(tmp_path)
    server.reply = lambda r: httpx.Response(200, text=f"you sent {r.headers['authorization']}")

    result = tools["http_request"](
        "get",
        "https://api.example.com/v1/x?k={{secret:API_KEY}}",
        headers={"Authorization": "Bearer {{secret:API_KEY}}"},
    )

    [request] = server.sent
    assert request.headers["authorization"] == f"Bearer {KEY}"
    assert str(request.url) == f"https://api.example.com/v1/x?k={KEY}"
    assert KEY not in json.dumps(result)
    assert result["status"] == 200 and "[REDACTED SECRET]" in result["body"]


@pytest.mark.parametrize(
    "form",
    [
        lambda v: v,
        lambda v: base64.b64encode(v.encode()).decode(),
        lambda v: base64.b64encode(v.encode()).decode().rstrip("="),
        lambda v: base64.urlsafe_b64encode(v.encode()).decode(),
        lambda v: v.encode().hex(),
        lambda v: quote(v, safe=""),
    ],
)
def test_the_value_is_blanked_out_of_a_response_in_its_common_encodings(
    form: Callable[[str], str], tmp_path: Path, server: _Server
) -> None:
    tools = _tools(tmp_path)
    server.reply = lambda r: httpx.Response(200, text="echo: " + form(KEY))

    result = tools["http_request"]("GET", "https://api.example.com/echo")

    assert "abcdef123456" not in result["body"] and "[REDACTED SECRET]" in result["body"]


@pytest.mark.parametrize(
    ("url", "headers", "message"),
    [
        ("https://evil.example.net/x", {"A": "{{secret:API_KEY}}"}, "may only be sent to"),
        ("https://api.example.com.evil.net/x", {"A": "{{secret:API_KEY}}"}, "may only be sent to"),
        ("http://api.example.com/x", {"A": "{{secret:API_KEY}}"}, "only sent over https"),
        (
            "https://api.example.com/x",
            {"A": "{{secret:OTHER_KEY}}"},
            "isn't one of this conversation",
        ),
        ("https://api.example.com/x", {"A": "{{secret:NOPE}}"}, "isn't one of this conversation"),
        ("https://{{secret:API_KEY}}.example.com/x", {}, "can't be part of the host"),
        ("ftp://api.example.com/x", {}, "isn't an http"),
    ],
)
def test_what_a_secret_is_never_sent_to(
    url: str, headers: dict[str, str], message: str, tmp_path: Path, server: _Server
) -> None:
    tools = _tools(tmp_path)

    with pytest.raises((SecretError, ValueError), match=message) as caught:
        tools["http_request"]("POST", url, headers=headers, body="{{secret:API_KEY}}")

    assert server.sent == []
    assert KEY not in str(caught.value)


def test_a_wildcard_host_covers_subdomains(tmp_path: Path, server: _Server) -> None:
    tools = _tools(tmp_path)

    tools["http_request"](
        "GET", "https://img.cdn.example.com/a", headers={"A": "{{secret:API_KEY}}"}
    )

    assert len(server.sent) == 1


def test_a_redirect_is_reported_not_followed_when_a_secret_was_used(
    tmp_path: Path, server: _Server
) -> None:
    tools = _tools(tmp_path)
    server.reply = lambda r: (
        httpx.Response(302, headers={"location": "https://elsewhere.example.net/"})
        if r.url.host == "api.example.com"
        else httpx.Response(200, text="landed")
    )

    with_secret = tools["http_request"](
        "GET", "https://api.example.com/x", headers={"A": "{{secret:API_KEY}}"}
    )
    without = tools["http_request"]("GET", "https://api.example.com/x")

    assert (with_secret["status"], with_secret["location"]) == (
        302,
        "https://elsewhere.example.net/",
    )
    assert (without["status"], without["body"]) == (200, "landed")


def test_a_secret_gone_from_the_keychain_is_an_error_naming_it(
    tmp_path: Path, server: _Server, keychain: _FakeKeyring
) -> None:
    tools = _tools(tmp_path)
    keychain.store.clear()

    with pytest.raises(SecretError, match="API_KEY"):
        tools["http_request"](
            "GET", "https://api.example.com/x", headers={"A": "{{secret:API_KEY}}"}
        )

    assert server.sent == []


def test_list_secrets_shows_only_this_conversations_secrets_and_never_a_value(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)
    nothing = {fn.__name__: fn for fn in build_http_tools(tmp_path, "unrelated")}

    listed = tools["list_secrets"]()

    assert listed == [{"name": "API_KEY", "hosts": ["api.example.com", "*.cdn.example.com"]}]
    assert KEY not in json.dumps(listed)
    assert nothing["list_secrets"]() == []


def test_the_middleware_blanks_a_result_an_error_and_a_command_update(tmp_path: Path) -> None:
    SecretStore(tmp_path).save("API_KEY", KEY, ["api.example.com"])
    middleware = _RedactToolResultsMiddleware(redactor(tmp_path))
    message = ToolMessage(content=f"key={KEY}", tool_call_id="1", name="t")
    error = ToolMessage(content=f"failed with {KEY}", tool_call_id="2", name="t", status="error")
    blocks = ToolMessage(content=[{"type": "text", "text": KEY}], tool_call_id="3", name="t")

    out = [middleware.wrap_tool_call(None, lambda _r, m=m: m) for m in (message, error, blocks)]

    assert out[0].content == "key=[REDACTED SECRET]"
    assert out[1].content == "failed with [REDACTED SECRET]" and out[1].status == "error"
    assert out[2].content == [{"type": "text", "text": "[REDACTED SECRET]"}]


def _call(args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": "http_request", "args": args, "id": "call_http"}]
    )


async def test_through_a_session_the_model_never_reads_the_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, server: _Server
) -> None:
    store = SecretStore(tmp_path / "state")
    store.save("API_KEY", KEY, ["api.example.com"])
    SessionEnvironments(tmp_path / "state").set("t1", {}, ["API_KEY"], store.names())
    server.reply = lambda r: httpx.Response(200, text=f"hello {r.headers['x-key']}")
    model = _Model(
        responses=[
            _call(
                {
                    "method": "GET",
                    "url": "https://api.example.com/me",
                    "headers": {"X-Key": "{{secret:API_KEY}}"},
                }
            ),
            AIMessage(content="done"),
        ]
    )
    session = _session(tmp_path, monkeypatch, None, model)  # type: ignore[arg-type]
    session.preapproved_tools = frozenset({"http_request"})
    socket = _Socket(answer=lambda payload: True)

    await _run(session, socket)

    [result] = [r for r in socket.of("tool_result") if r["tool_name"] == "http_request"]
    shown = json.dumps(result, ensure_ascii=False)
    assert KEY not in shown and "[REDACTED SECRET]" in shown
    assert server.sent[0].headers["x-key"] == KEY
    state = await session.lg_agent.aget_state(session.config)
    assert KEY not in repr(state.values["messages"])


def test_a_value_replaced_at_once_is_blanked_not_the_one_before(tmp_path: Path) -> None:
    store = SecretStore(tmp_path)
    redact = redactor(tmp_path)
    store.save("K", "aaaaaaaa-1111", ["a.com"])
    assert redact("x aaaaaaaa-1111") == "x [REDACTED SECRET]"

    store.save("K", "bbbbbbbb-2222", ["a.com"])

    assert redact("x bbbbbbbb-2222") == "x [REDACTED SECRET]"
    assert redact("x aaaaaaaa-1111") == "x aaaaaaaa-1111"
    store.delete("K")
    assert redact("x bbbbbbbb-2222") == "x bbbbbbbb-2222"


def test_a_secret_saved_before_the_minimum_length_is_not_sent(
    tmp_path: Path, server: _Server, keychain: _FakeKeyring
) -> None:
    tools = _tools(tmp_path)
    keychain.store[("coscribe", "secret:API_KEY")] = "short"

    with pytest.raises(SecretError, match="shorter than 8"):
        tools["http_request"](
            "GET", "https://api.example.com/x", headers={"A": "{{secret:API_KEY}}"}
        )

    assert server.sent == []


def test_the_secret_tools_are_deferred_not_core() -> None:
    from coscribe.coordinator import CORE_TOOL_NAMES  # noqa: PLC0415

    assert not {"http_request", "list_secrets"} & CORE_TOOL_NAMES


def _bound(session: Any) -> list[tuple[str, str]]:
    return [
        (getattr(t, "__name__", getattr(t, "name", "")), t.__doc__ or getattr(t, "description", ""))
        for t in session._build_lg_tools(session.model)
    ]


def test_what_the_provider_caches_is_the_same_whatever_secrets_a_conversation_has(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_secrets = _session(tmp_path / "a", monkeypatch, None, _Model(responses=[]))  # type: ignore[arg-type]
    store = SecretStore(tmp_path / "a" / "state")
    store.save("API_KEY", KEY, ["api.example.com"])
    SessionEnvironments(tmp_path / "a" / "state").set("t1", {"X": "1"}, ["API_KEY"], store.names())
    without = _session(tmp_path / "b", monkeypatch, None, _Model(responses=[]))  # type: ignore[arg-type]

    assert _bound(with_secrets) == _bound(without)
    assert with_secrets._instructions == without._instructions
    assert "API_KEY" not in with_secrets._instructions

"""Browser sign-in for remote MCP connectors: the MCP spec's OAuth 2.1 (the
server's own discovery documents, dynamic client registration, PKCE), which
the MCP SDK's `OAuthClientProvider` implements. This module supplies what
the SDK leaves to the host: where tokens live, how the sign-in page is
opened, and how the redirect back to coscribe reaches the waiting request.

A connector counts as "one click" only if its server accepts dynamic client
registration -- a user never has to create an app anywhere, and nothing is
installed locally. Servers that need a pre-registered app (Slack, Google
Workspace, Box) are out by design.

Servers that don't accept dynamic registration (HubSpot, Google Workspace)
work with an app the user or their organization registered with the
service: its client id (and secret, if it has one) is saved once, keyed by
the service's name for it, and used in place of a registration. Those
services check the redirect address exactly, and the desktop app listens on
a different port each start, so such a sign-in is received on a fixed
loopback port that is open only while it lasts.

Tokens and the registered client are stored through `runtime/secrets.py`
(OS keychain, plaintext fallback with 0600), so a restart reconnects
without asking again; a refresh token that no longer works surfaces as
`NeedsSignIn`, because opening a browser at startup, with nobody waiting
for it, would be wrong.
"""

from __future__ import annotations

import asyncio
import json
import re
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from ..runtime.secrets import delete_secret, harden_file_permissions, resolve_secret, store_secret

# The MCP SDK and httpx are imported where they are used: this module is
# loaded for every server start, and most people have no remote connector.
if TYPE_CHECKING:
    import httpx
    from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

# Long enough to sign in, find a code on a phone, approve an admin prompt.
SIGN_IN_TIMEOUT_SECONDS = 300.0
_CLIENT_NAME = "coscribe"
# Only used when a refresh needs a client's metadata and none was saved: the
# SDK requires one redirect URI, and nothing is ever sent to it.
_PLACEHOLDER_REDIRECT = "http://127.0.0.1/api/mcp/oauth/callback"


# Rarely used by anything else, so an app registered with a service can name
# one redirect address that is always the same.
CALLBACK_PORT = 47821
CALLBACK_PATH = "/api/mcp/oauth/callback"
APP_REDIRECT_URI = f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}"


class CallbackPortBusy(Exception):
    """Something else is listening on the fixed sign-in port."""


class NeedsSignIn(Exception):
    """The connector has no usable credentials and nobody is signing in."""


@dataclass
class SignIn:
    """One attempt to sign in to one connector."""

    name: str
    redirect_uri: str
    url: str | None = None
    # Added to the authorization address: the scopes a service needs named
    # (its discovery documents don't) and its own extra parameters.
    scope: str | None = None
    auth_params: dict[str, str] = field(default_factory=dict)
    # Set once the server has answered, whether or not it worked.
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    url_ready: asyncio.Event = field(default_factory=asyncio.Event)
    error: str | None = None
    _code: asyncio.Future[tuple[str, str | None]] = field(
        default_factory=lambda: asyncio.get_running_loop().create_future()
    )


class _Storage:
    """The SDK's `TokenStorage`, kept in one small file per connector. The
    file holds only what `store_secret` returned for each value."""

    def __init__(self, path: Path, name: str) -> None:
        self._path = path
        self._name = name

    def _read(self) -> dict[str, Any]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data), encoding="utf-8")
        harden_file_permissions(self._path)

    def _save(self, key: str, value: str) -> None:
        data = self._read()
        delete_secret(data.get(key))
        data[key] = store_secret(f"mcp-oauth:{self._name}:{key}", value)
        self._write(data)

    def drop(self, key: str) -> None:
        data = self._read()
        if key in data:
            delete_secret(data.pop(key))
            self._write(data)

    def load(self, key: str) -> str | None:
        return resolve_secret(self._read().get(key))

    async def get_tokens(self) -> OAuthToken | None:
        from mcp.shared.auth import OAuthToken

        raw = self.load("tokens")
        return OAuthToken.model_validate_json(raw) if raw else None

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._save("tokens", tokens.model_dump_json(exclude_none=True))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self.load("client")
        return OAuthClientInformationFull.model_validate_json(raw) if raw else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._save("client", client_info.model_dump_json(exclude_none=True))

    def clear(self) -> None:
        self.drop("tokens")
        self.drop("client")
        self._path.unlink(missing_ok=True)


def _file_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name) + ".json"


class McpOAuth:
    """Sign-ins for every OAuth connector of one coscribe server."""

    def __init__(
        self, state_dir: Path, open_browser: Callable[[str], object] | None = None
    ) -> None:
        self._dir = state_dir / "mcp_oauth"
        self._open_browser = open_browser
        self._active: dict[str, SignIn] = {}
        self._by_state: dict[str, SignIn] = {}
        self._last_error: dict[str, str] = {}

    def _storage(self, name: str) -> _Storage:
        return _Storage(self._dir / _file_name(name), name)

    def pending(self, name: str) -> SignIn | None:
        return self._active.get(name)

    def last_error(self, name: str) -> str | None:
        return self._last_error.get(name)

    def _app_storage(self, group: str) -> _Storage:
        return _Storage(self._dir / "apps" / _file_name(group), f"app:{group}")

    def set_app(self, group: str, client_id: str, client_secret: str | None) -> None:
        storage = self._app_storage(group)
        storage._save("client_id", client_id)
        if client_secret:
            storage._save("client_secret", client_secret)
        else:
            storage.drop("client_secret")

    def app(self, group: str) -> tuple[str, str | None] | None:
        storage = self._app_storage(group)
        client_id = storage.load("client_id")
        return (client_id, storage.load("client_secret")) if client_id else None

    def forget_app(self, group: str) -> None:
        storage = self._app_storage(group)
        storage.drop("client_id")
        storage.drop("client_secret")
        storage._path.unlink(missing_ok=True)

    def app_groups(self) -> list[str]:
        apps = self._dir / "apps"
        if not apps.is_dir():
            return []
        return sorted(p.stem for p in apps.glob("*.json") if self.app(p.stem) is not None)

    def has_credentials(self, name: str) -> bool:
        return self._storage(name).load("tokens") is not None

    def forget(self, name: str) -> None:
        self.cancel(name)
        self._storage(name).clear()
        self._last_error.pop(name, None)

    def cancel(self, name: str) -> None:
        old = self._active.pop(name, None)
        if old is None:
            return
        for state in [s for s, flow in self._by_state.items() if flow is old]:
            del self._by_state[state]
        if not old._code.done():
            from mcp.client.auth import OAuthFlowError

            # Not cancelled: the waiting connection has to end with an error,
            # or its caller would wait out the whole timeout.
            old._code.set_exception(OAuthFlowError("A newer sign-in replaced this one."))
        old.url_ready.set()

    async def begin(
        self,
        name: str,
        redirect_uri: str,
        *,
        app: str | None = None,
        scope: str | None = None,
        auth_params: dict[str, str] | None = None,
    ) -> SignIn:
        """Start signing in to `name`. Tokens from before are dropped: the
        user asked to sign in, so the ones on file didn't work. The
        registered client is kept unless it was registered for another
        redirect address -- the desktop app's server listens on a different
        port each time it starts."""
        self.cancel(name)
        self._last_error.pop(name, None)
        storage = self._storage(name)
        storage.drop("tokens")
        if app is not None:
            await self._seed_app(storage, app, redirect_uri)
        client = await storage.get_client_info()
        if client is not None and redirect_uri not in {str(u) for u in client.redirect_uris or []}:
            storage.drop("client")
        flow = SignIn(
            name=name, redirect_uri=redirect_uri, scope=scope, auth_params=auth_params or {}
        )
        self._active[name] = flow
        return flow

    async def _seed_app(self, storage: _Storage, group: str, redirect_uri: str) -> None:
        """Stores the registered app as the connector's client, so the SDK
        goes straight to the sign-in page instead of registering one."""
        from mcp.shared.auth import OAuthClientInformationFull
        from pydantic import AnyUrl

        credentials = self.app(group)
        if credentials is None:
            raise NeedsSignIn("Set up the app for this connector first.")
        client_id, client_secret = credentials
        await storage.set_client_info(
            OAuthClientInformationFull(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uris=[AnyUrl(redirect_uri)],
                token_endpoint_auth_method="client_secret_post" if client_secret else "none",
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                client_name=_CLIENT_NAME,
            )
        )

    def finish(self, flow: SignIn, error: str | None) -> None:
        flow.error = error
        if error and self._active.get(flow.name) in (flow, None):
            self._last_error[flow.name] = error
        if self._active.get(flow.name) is flow:
            del self._active[flow.name]
        for state in [s for s, f in self._by_state.items() if f is flow]:
            del self._by_state[state]
        flow.finished.set()
        flow.url_ready.set()

    def complete(self, state: str, code: str | None, error: str | None) -> str | None:
        """The redirect back from the provider. Returns the connector's
        name, or None if no sign-in is waiting for this `state`."""
        from mcp.client.auth import OAuthFlowError

        flow = self._by_state.pop(state, None)
        if flow is None or flow._code.done():
            return None
        if code and not error:
            flow._code.set_result((code, state))
        else:
            flow._code.set_exception(OAuthFlowError(error or "The sign-in was refused."))
        return flow.name

    async def auth_for(self, name: str, server_url: str) -> httpx.Auth:
        from mcp.client.auth import OAuthClientProvider
        from mcp.shared.auth import OAuthClientMetadata
        from pydantic import AnyUrl

        storage = self._storage(name)
        flow = self._active.get(name)
        known = await storage.get_client_info()
        redirect = flow.redirect_uri if flow else None
        if redirect is None and known is not None and known.redirect_uris:
            redirect = str(known.redirect_uris[0])
        metadata = OAuthClientMetadata(
            redirect_uris=[AnyUrl(redirect or _PLACEHOLDER_REDIRECT)],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            client_name=_CLIENT_NAME,
        )

        async def redirect_handler(url: str) -> None:
            if flow is None or self._active.get(name) is not flow:
                raise NeedsSignIn(f"{name} needs you to sign in again.")
            parsed = urlparse(url)
            state = parse_qs(parsed.query).get("state", [""])[0]
            if flow.scope or flow.auth_params:
                query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
                if flow.scope:
                    query["scope"] = flow.scope
                query.update(flow.auth_params)
                url = urlunparse(parsed._replace(query=urlencode(query)))
            self._by_state[state] = flow
            flow.url = url
            flow.url_ready.set()
            (self._open_browser or webbrowser.open)(url)

        async def callback_handler() -> tuple[str, str | None]:
            from mcp.client.auth import OAuthFlowError

            assert flow is not None
            try:
                return await asyncio.wait_for(flow._code, SIGN_IN_TIMEOUT_SECONDS)
            except TimeoutError:
                raise OAuthFlowError("Timed out waiting for you to sign in.") from None

        return OAuthClientProvider(
            server_url=server_url,
            client_metadata=metadata,
            storage=storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
            timeout=SIGN_IN_TIMEOUT_SECONDS,
        )


class LoopbackCallback:
    """Receives the redirect for an app registered with a service, on the
    fixed port that app names. Open only while a sign-in is waiting; any
    other request than the callback is refused."""

    def __init__(self, respond: Callable[[str, str | None, str | None], tuple[int, str]]) -> None:
        self._respond = respond
        self._server: asyncio.AbstractServer | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    async def start(self) -> None:
        if self._server is not None:
            return
        try:
            # "localhost" binds every address it names (::1 and 127.0.0.1), so
            # whichever one the browser picks reaches it.
            self._server = await asyncio.start_server(self._handle, "localhost", CALLBACK_PORT)
        except OSError as exc:
            raise CallbackPortBusy(
                f"Port {CALLBACK_PORT} is used by another program. Close it and try again."
            ) from exc

    async def stop(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
            parts = request_line.split()
            target = urlparse(parts[1]) if len(parts) >= 2 and parts[0] == "GET" else None
            if target is None or target.path != CALLBACK_PATH:
                status, body = 404, "Not found"
            else:
                query = parse_qs(target.query)
                status, body = self._respond(
                    query.get("state", [""])[0],
                    query.get("code", [None])[0],
                    query.get("error", [None])[0],
                )
            payload = body.encode("utf-8")
            writer.write(
                f"HTTP/1.1 {status} OK\r\nContent-Type: text/html; charset=utf-8\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                + payload
            )
            await writer.drain()
        except (TimeoutError, ConnectionError):
            pass
        finally:
            writer.close()

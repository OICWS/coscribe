"""Browser sign-in for remote MCP connectors: the MCP spec's OAuth 2.1 (the
server's own discovery documents, dynamic client registration, PKCE), which
the MCP SDK's `OAuthClientProvider` implements. This module supplies what
the SDK leaves to the host: where tokens live, how the sign-in page is
opened, and how the redirect back to coscribe reaches the waiting request.

A connector counts as "one click" only if its server accepts dynamic client
registration -- a user never has to create an app anywhere, and nothing is
installed locally. Servers that need a pre-registered app (Slack, Google
Workspace, Box) are out by design.

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
from urllib.parse import parse_qs, urlparse

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


class NeedsSignIn(Exception):
    """The connector has no usable credentials and nobody is signing in."""


@dataclass
class SignIn:
    """One attempt to sign in to one connector."""

    name: str
    redirect_uri: str
    url: str | None = None
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

    async def begin(self, name: str, redirect_uri: str) -> SignIn:
        """Start signing in to `name`. Tokens from before are dropped: the
        user asked to sign in, so the ones on file didn't work. The
        registered client is kept unless it was registered for another
        redirect address -- the desktop app's server listens on a different
        port each time it starts."""
        self.cancel(name)
        self._last_error.pop(name, None)
        storage = self._storage(name)
        storage.drop("tokens")
        client = await storage.get_client_info()
        if client is not None and redirect_uri not in {str(u) for u in client.redirect_uris or []}:
            storage.drop("client")
        flow = SignIn(name=name, redirect_uri=redirect_uri)
        self._active[name] = flow
        return flow

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
            state = parse_qs(urlparse(url).query).get("state", [""])[0]
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

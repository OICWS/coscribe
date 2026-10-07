"""A small MCP server that follows the spec's OAuth 2.1 (discovery, dynamic
client registration, PKCE) and only answers a caller holding its token --
what a hosted connector looks like from coscribe's side. The "sign-in page"
approves at once and redirects back, as a browser would after the user
clicked Allow."""

import secrets
import socket
import threading
import time
from contextlib import contextmanager
from typing import Any

import uvicorn
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions
from mcp.server.fastmcp import FastMCP
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl


class _Provider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(self) -> None:
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.codes: dict[str, AuthorizationCode] = {}
        self.access: dict[str, AccessToken] = {}
        self.refresh: dict[str, RefreshToken] = {}
        self.registrations = 0
        self.token_lifetime = 3600

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.registrations += 1
        assert client_info.client_id is not None
        self.clients[client_info.client_id] = client_info

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        code = secrets.token_urlsafe(24)
        self.codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + 300,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self.codes.get(authorization_code)

    def _issue(self, client_id: str, scopes: list[str]) -> OAuthToken:
        access, refresh = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        self.access[access] = AccessToken(
            token=access,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + self.token_lifetime,
        )
        self.refresh[refresh] = RefreshToken(token=refresh, client_id=client_id, scopes=scopes)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=self.token_lifetime,
            refresh_token=refresh,
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        del self.codes[authorization_code.code]
        return self._issue(client.client_id or "", authorization_code.scopes)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        return self.refresh.get(refresh_token)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        del self.refresh[refresh_token.token]
        return self._issue(client.client_id or "", scopes or refresh_token.scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        return self.access.get(token)

    async def revoke_token(self, token: Any) -> None:
        self.access.pop(getattr(token, "token", ""), None)
        self.refresh.pop(getattr(token, "token", ""), None)


class OAuthMcpServer:
    def __init__(self, port: int, with_tool: bool = True) -> None:
        self.provider = _Provider()
        self.url = f"http://127.0.0.1:{port}"
        mcp = FastMCP(
            "oauth-test",
            host="127.0.0.1",
            port=port,
            auth_server_provider=self.provider,
            auth=AuthSettings(
                issuer_url=AnyHttpUrl(self.url),
                resource_server_url=AnyHttpUrl(f"{self.url}/mcp"),
                client_registration_options=ClientRegistrationOptions(enabled=True),
                validate_token_resource=False,
            ),
        )

        if with_tool:

            @mcp.tool()
            def whoami() -> str:
                """Say who is signed in."""
                return "signed-in-user"

        self.mcp = mcp
        self.mcp_url = f"{self.url}/mcp"


@contextmanager
def running_oauth_mcp_server(with_tool: bool = True) -> Any:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = OAuthMcpServer(port, with_tool)
    uv = uvicorn.Server(
        uvicorn.Config(
            server.mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"
        )
    )
    thread = threading.Thread(target=uv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if uv.started:
            break
        time.sleep(0.05)
    try:
        yield server
    finally:
        uv.should_exit = True
        thread.join(timeout=5)

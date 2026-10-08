# 0004: Connectors sign in once, in the user's own browser

Status: accepted (decided 2026-10-07, recorded 2026-10-08). Implementation: `runtime_lg/mcp_oauth.py`, `web/connector_catalog.py`.

## Context
A connector should work wherever the person can already open the service
themselves, without installing anything or involving company IT. Many services
refuse unknown clients.

## Decision
The curated catalog lists only hosted (remote) MCP servers the user signs in to on
the service's own page, in their own browser: OAuth 2.1 with PKCE and dynamic
client registration (the MCP SDK's `OAuthClientProvider`). Registering a client is
proven by a real attempt: a listed registration address alone proved nothing
(Dropbox lists one and refuses). Services that don't register clients on their own
(HubSpot, Google Workspace) get a guided one-time setup where the user or their
organization registers an app and gives coscribe its client id, received on a
fixed loopback port that is open only during the sign-in. Tokens go through
`runtime/secrets.py`; a startup reconnect never opens a browser.

## Consequences
- Never ship a shared client secret or a coscribe-owned app that needs a vendor
  review.
- Left out on purpose: Slack, Box, Zoom, Salesforce and others that need a
  pre-registered app we can't hand out, and Microsoft 365 where a tenant disallows
  third-party apps. Anything else is addable by hand as a custom connector.
- A new catalog entry is verified against the live server (2026-10 entries were).

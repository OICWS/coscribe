"""Shared corporate-proxy detection -- see ROADMAP.md's "Unified
corporate-proxy support" backlog item: LLM provider SDKs already read
HTTP_PROXY/HTTPS_PROXY themselves (httpx/grpc's own env-var support), but
web_search (tools/websearch.py) and the Browser panel's headless Chrome
(web/browser_panel.py) each need it forwarded explicitly -- ddgs's
DDGS.text() has its own separate proxy= kwarg unrelated to the standard
env vars, and Chromium's env-var-based proxy auto-detection is
unreliable on Windows (this project's actual target platform), normally
needing an explicit --proxy-server= launch flag instead.
"""

from __future__ import annotations

from urllib.request import getproxies


def configured_proxy() -> str | None:
    """HTTPS_PROXY (any case), falling back to HTTP_PROXY, if the user
    has one set -- via urllib's own getproxies() rather than a hand-
    rolled os.environ lookup so this also picks up Windows' system/
    registry proxy setting, not just an env var."""
    proxies = getproxies()
    return proxies.get("https") or proxies.get("http")

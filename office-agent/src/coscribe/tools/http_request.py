"""http_request and list_secrets: calling an API with a secret the model never
sees.

The model writes `{{secret:NAME}}` where a key goes. coscribe puts the value
in at the moment of the request, and only if the secret is one this
conversation was given and the request goes to a host the secret is bound to,
over https. What comes back is blanked of every stored secret's value (and its
common encodings) before the model reads it. Redirects are not followed when a
secret was used, so a value can't be carried on to another host.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from ..runtime.proxy import configured_proxy
from ..runtime.secret_store import (
    SecretError,
    SecretStore,
    SessionEnvironments,
    host_allowed,
    placeholders_in,
    redactor,
    substitute,
)
from ..runtime.types import tool_metadata

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"})
_MAX_BODY_CHARS = 20000
_TIMEOUT_SECONDS = 30.0
_USER_AGENT = "coscribe/1 (+http_request)"


def build_http_tools(state_dir: str | Path, thread_id: str) -> list[Callable[..., Any]]:
    store = SecretStore(state_dir)
    environments = SessionEnvironments(state_dir)
    redact = redactor(state_dir)

    def list_secrets() -> list[dict[str, Any]]:
        """The secrets the user gave this conversation: each one's name and the
        hosts it may be sent to. The values are never shown to anyone but the
        request they are put into -- use a secret as {{secret:NAME}} in an
        http_request header, body or url. Empty when none were given."""
        allowed = environments.get(thread_id)["secrets"]
        return [
            {"name": name, "hosts": store.hosts_of(name) or []}
            for name in allowed
            if name in store.names()
        ]

    def http_request(
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: str | None = None,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> dict[str, Any]:
        """Make an HTTP request and return the status and the response text.
        To use a secret the user gave this conversation (see list_secrets),
        write {{secret:NAME}} in a header value, the body or the url's query:
        it is replaced when the request is sent, only for that secret's own
        hosts and only over https, and redirects are then not followed
        (the answer reports the Location instead). You never see the value.

        Args:
            method: GET, POST, PUT, PATCH, DELETE or HEAD
            url: the full http(s) address; the host itself can't hold a placeholder
            headers: header names to values, e.g. {"Authorization": "Bearer {{secret:API_KEY}}"}
            body: the request body as text, e.g. JSON
            timeout: seconds to wait, at most 120
        """
        method = method.upper()
        if method not in _METHODS:
            raise ValueError(
                f"{method!r} isn't a method this tool sends: {', '.join(sorted(_METHODS))}"
            )
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"{url!r} isn't an http(s) address")
        if "{{" in parts.netloc or "%7b" in parts.netloc.lower():
            raise ValueError("A placeholder can't be part of the host.")
        headers = dict(headers or {})
        wanted = placeholders_in(url) | placeholders_in(body or "")
        for value in headers.values():
            wanted |= placeholders_in(value)
        values = _values_for(wanted, parts.hostname, parts.scheme)
        sent_url = substitute(url, values)
        sent_headers = {k: substitute(v, values) for k, v in headers.items()}
        sent_body = substitute(body, values) if body is not None else None
        sent_headers.setdefault("User-Agent", _USER_AGENT)
        with httpx.Client(
            proxy=configured_proxy(),
            follow_redirects=not values,
            timeout=min(max(float(timeout), 1.0), 120.0),
        ) as client:
            response = client.request(
                method,
                sent_url,
                headers=sent_headers,
                content=sent_body.encode("utf-8") if sent_body is not None else None,
            )
        text = response.text if method != "HEAD" else ""
        result: dict[str, Any] = {
            "status": response.status_code,
            "content_type": response.headers.get("content-type", ""),
            "body": redact(text[:_MAX_BODY_CHARS]),
            "truncated": len(text) > _MAX_BODY_CHARS,
        }
        location = response.headers.get("location")
        if location:
            result["location"] = redact(location)
        return result

    def _values_for(names: set[str], host: str, scheme: str) -> dict[str, str]:
        if not names:
            return {}
        given = set(environments.get(thread_id)["secrets"])
        values: dict[str, str] = {}
        for name in sorted(names):
            if name not in given:
                raise SecretError(
                    f"The secret {name} isn't one of this conversation's. "
                    "list_secrets shows which are; the user adds one in the session's "
                    "Edit environment."
                )
            hosts = store.hosts_of(name)
            if hosts is None:
                raise SecretError(f"There is no secret named {name}.")
            if not any(host_allowed(pattern, host) for pattern in hosts):
                raise SecretError(
                    f"The secret {name} may only be sent to {', '.join(hosts)}, not {host}."
                )
            if scheme != "https":
                raise SecretError(f"The secret {name} is only sent over https.")
            values[name] = store.resolve(name)
        return values

    return [
        tool_metadata(list_secrets, risk_category="READ", category="web"),
        tool_metadata(http_request, risk_category="EXTERNAL", category="web"),
    ]

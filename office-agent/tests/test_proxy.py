"""Tests for runtime/proxy.py -- the shared corporate-proxy detection
used by both tools/websearch.py and web/browser_panel.py (see that
module's own docstring for why these two needed it forwarded explicitly
rather than relying on their own libraries' unrelated env-var handling).
"""

import pytest

from coscribe.runtime.proxy import configured_proxy

_PROXY_ENV_VARS = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")


def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _PROXY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_configured_proxy_is_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxy_env(monkeypatch)

    assert configured_proxy() is None


def test_configured_proxy_reads_uppercase_https_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")

    assert configured_proxy() == "http://proxy.example.com:8080"


def test_configured_proxy_reads_lowercase_https_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("https_proxy", "http://proxy.example.com:8080")

    assert configured_proxy() == "http://proxy.example.com:8080"


def test_configured_proxy_falls_back_to_http_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example.com:3128")

    assert configured_proxy() == "http://proxy.example.com:3128"


def test_configured_proxy_prefers_https_over_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://plain-proxy.example.com:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://tls-proxy.example.com:8080")

    assert configured_proxy() == "http://tls-proxy.example.com:8080"

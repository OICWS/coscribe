from typing import Any

import pytest

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.websearch import build_websearch_tools, web_search


def test_web_search_normalizes_ddgs_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_text(self: Any, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured["query"] = query
        captured["kwargs"] = kwargs
        return [
            {
                "title": "Paris - Wikipedia",
                "href": "https://en.wikipedia.org/wiki/Paris",
                "body": "Paris is the capital of France.",
            },
            {"title": "Another result", "href": "https://example.com", "body": "some snippet"},
        ]

    monkeypatch.setattr("ddgs.ddgs.DDGS.text", _fake_text)

    results = web_search("capital of France")

    assert captured["query"] == "capital of France"
    assert "backend" not in captured["kwargs"]  # "auto" (ddgs's own default) -- see websearch.py
    assert captured["kwargs"]["max_results"] == 5
    assert results == [
        {
            "title": "Paris - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Paris",
            "snippet": "Paris is the capital of France.",
        },
        {"title": "Another result", "url": "https://example.com", "snippet": "some snippet"},
    ]


def test_web_search_respects_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_text(self: Any, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr("ddgs.ddgs.DDGS.text", _fake_text)

    web_search("anything", max_results=2)

    assert captured["kwargs"]["max_results"] == 2


def test_web_search_handles_missing_fields_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_text(self: Any, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return [{}]  # a backend returning a sparse result shouldn't crash the tool

    monkeypatch.setattr("ddgs.ddgs.DDGS.text", _fake_text)

    results = web_search("anything")

    assert results == [{"title": "", "url": "", "snippet": ""}]


def test_web_search_forwards_https_proxy_to_ddgs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, proxy: str | None = None, **kwargs: Any) -> None:
        captured["proxy"] = proxy

    def _fake_text(self: Any, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr("ddgs.ddgs.DDGS.__init__", _fake_init)
    monkeypatch.setattr("ddgs.ddgs.DDGS.text", _fake_text)
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example.com:8080")

    web_search("anything")

    assert captured["proxy"] == "http://proxy.example.com:8080"


def test_web_search_passes_no_proxy_when_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, proxy: str | None = None, **kwargs: Any) -> None:
        captured["proxy"] = proxy

    def _fake_text(self: Any, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr("ddgs.ddgs.DDGS.__init__", _fake_init)
    monkeypatch.setattr("ddgs.ddgs.DDGS.text", _fake_text)
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)

    web_search("anything")

    assert captured["proxy"] is None


def test_web_search_tool_metadata() -> None:
    tools = build_websearch_tools()
    assert len(tools) == 1
    metadata = get_tool_metadata(tools[0])
    assert metadata.risk_category == "READ"
    assert metadata.category == "web"
    assert metadata.requires_approval is False

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
    assert [t.__name__ for t in tools] == ["web_search", "read_web_page"]
    for tool in tools:
        metadata = get_tool_metadata(tool)
        assert metadata.risk_category == "READ"
        assert metadata.category == "web"
        assert metadata.requires_approval is False


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    import httpx

    real_client = httpx.Client

    def client(**kwargs: Any) -> Any:
        kwargs.pop("proxy", None)
        return real_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("coscribe.tools.websearch.httpx.Client", client)


def test_read_web_page_returns_the_main_text_as_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from coscribe.tools.websearch import read_web_page

    html = (
        "<html><head><title>Q3 results</title><script>track()</script></head><body>"
        "<nav>Home | About</nav><main><h1>Q3 results</h1><p>Revenue grew <b>12%</b>.</p>"
        "</main><footer>(c) 2026</footer></body></html>"
    )
    _serve(monkeypatch, lambda request: httpx.Response(200, html=html))

    page = read_web_page("https://example.com/q3", max_chars=20)

    assert page["title"] == "Q3 results"
    assert page["content"] == "# Q3 results\n\nRevenu"
    assert page["next_start"] == 20
    rest = read_web_page("https://example.com/q3", start=20)
    assert rest["content"] == "e grew **12%**." and rest["next_start"] is None
    assert "Home" not in rest["content"] and "track" not in rest["content"]


def test_read_web_page_refuses_what_isnt_a_page(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from coscribe.tools.websearch import read_web_page

    _serve(
        monkeypatch,
        lambda request: httpx.Response(
            200, content=b"%PDF", headers={"content-type": "application/pdf"}
        ),
    )
    with pytest.raises(ValueError, match="is application/pdf, not a page to read"):
        read_web_page("https://example.com/report.pdf")
    with pytest.raises(ValueError, match="isn't an http"):
        read_web_page("file:///etc/passwd")

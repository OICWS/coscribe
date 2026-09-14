import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools import images as images_module
from coscribe.tools.images import (
    ImageToolkit,
    build_image_tools,
    search_images,
    search_licensed_images,
)


def _png_bytes(width: int = 8, height: int = 6) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color="red").save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, body: bytes, *, content_type: str = "image/png", status: int = 200):
        self._body = body
        self.headers = {"content-type": content_type}
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            request = httpx.Request("GET", "https://example.com/image.png")
            response = httpx.Response(self._status, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def iter_bytes(self, chunk_size: int) -> Iterator[bytes]:
        for start in range(0, len(self._body), chunk_size):
            yield self._body[start : start + chunk_size]


class _FakeStreamContext:
    def __init__(self, response: _FakeResponse):
        self._response = response

    def __enter__(self) -> _FakeResponse:
        return self._response

    def __exit__(self, *exc_info: object) -> None:
        return None


class _FakeClient:
    def __init__(self, response: _FakeResponse, **kwargs: Any):
        self._response = response

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def stream(self, method: str, url: str) -> _FakeStreamContext:
        return _FakeStreamContext(self._response)


def _patch_httpx_client(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> None:
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(response, **kwargs))


def test_search_images_normalizes_ddgs_result_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_images(self: Any, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured["query"] = query
        captured["kwargs"] = kwargs
        return [
            {
                "title": "Mountain sunset",
                "image": "https://example.com/full.jpg",
                "thumbnail": "https://example.com/thumb.jpg",
                "url": "https://example.com/page",
                "width": 1920,
                "height": 1080,
                "source": "Bing",
            }
        ]

    monkeypatch.setattr("ddgs.ddgs.DDGS.images", _fake_images)

    results = search_images("mountain landscape")

    assert captured["query"] == "mountain landscape"
    assert captured["kwargs"]["max_results"] == 5
    assert results == [
        {
            "title": "Mountain sunset",
            "image_url": "https://example.com/full.jpg",
            "thumbnail_url": "https://example.com/thumb.jpg",
            "source_page_url": "https://example.com/page",
            "width": 1920,
            "height": 1080,
        }
    ]


def test_search_images_respects_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_images(self: Any, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        captured["kwargs"] = kwargs
        return []

    monkeypatch.setattr("ddgs.ddgs.DDGS.images", _fake_images)

    search_images("anything", max_results=2)

    assert captured["kwargs"]["max_results"] == 2


def test_search_images_handles_missing_fields_gracefully(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_images(self: Any, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        return [{}]

    monkeypatch.setattr("ddgs.ddgs.DDGS.images", _fake_images)

    results = search_images("anything")

    assert results == [
        {
            "title": "",
            "image_url": "",
            "thumbnail_url": "",
            "source_page_url": "",
            "width": "",
            "height": "",
        }
    ]


def test_download_image_saves_a_valid_image_and_reports_real_dimensions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx_client(monkeypatch, _FakeResponse(_png_bytes(8, 6)))
    toolkit = ImageToolkit(tmp_path)

    result = toolkit.download_image("https://example.com/image.png", "bg.png")

    assert result["width"] == 8
    assert result["height"] == 6
    assert result["content_type"] == "image/png"
    assert (tmp_path / "bg.png").is_file()
    assert result["bytes_written"] == (tmp_path / "bg.png").stat().st_size


def test_download_image_rejects_non_image_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx_client(
        monkeypatch, _FakeResponse(b"<html>not an image</html>", content_type="text/html")
    )
    toolkit = ImageToolkit(tmp_path)

    with pytest.raises(ValueError, match="did not return a valid image"):
        toolkit.download_image("https://example.com/page.html", "bg.png")
    assert not (tmp_path / "bg.png").exists()


def test_download_image_rejects_http_error_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx_client(monkeypatch, _FakeResponse(b"", status=404))
    toolkit = ImageToolkit(tmp_path)

    with pytest.raises(httpx.HTTPStatusError):
        toolkit.download_image("https://example.com/missing.png", "bg.png")


def test_download_image_rejects_oversized_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(images_module, "_MAX_IMAGE_BYTES", 100)
    _patch_httpx_client(monkeypatch, _FakeResponse(b"x" * 500))
    toolkit = ImageToolkit(tmp_path)

    with pytest.raises(ValueError, match="exceeds"):
        toolkit.download_image("https://example.com/huge.png", "bg.png")
    assert not (tmp_path / "bg.png").exists()


def test_download_image_rejects_non_http_scheme(tmp_path: Path) -> None:
    toolkit = ImageToolkit(tmp_path)

    with pytest.raises(ValueError, match="Only http/https"):
        toolkit.download_image("file:///etc/passwd", "bg.png")


def test_download_image_path_cannot_escape_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx_client(monkeypatch, _FakeResponse(_png_bytes()))
    toolkit = ImageToolkit(tmp_path)

    with pytest.raises(PermissionError):
        toolkit.download_image("https://example.com/image.png", "../outside.png")


def test_download_image_does_not_overwrite_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx_client(monkeypatch, _FakeResponse(_png_bytes()))
    toolkit = ImageToolkit(tmp_path)
    toolkit.download_image("https://example.com/image.png", "bg.png")

    with pytest.raises(FileExistsError):
        toolkit.download_image("https://example.com/image.png", "bg.png", overwrite=False)


def test_image_tools_metadata() -> None:
    tools = {tool.__name__: tool for tool in build_image_tools("/tmp")}  # type: ignore[attr-defined]

    search_metadata = get_tool_metadata(tools["search_images"])
    assert search_metadata.risk_category == "READ"
    assert search_metadata.category == "web"
    assert search_metadata.requires_approval is False

    licensed_search_metadata = get_tool_metadata(tools["search_licensed_images"])
    assert licensed_search_metadata.risk_category == "READ"
    assert licensed_search_metadata.category == "web"
    assert licensed_search_metadata.requires_approval is False

    download_metadata = get_tool_metadata(tools["download_image"])
    assert download_metadata.risk_category == "EXTERNAL"
    assert download_metadata.category == "web"
    assert download_metadata.requires_approval is True


# --- search_licensed_images ---

_OPENVERSE_PAYLOAD = {
    "results": [
        {
            "title": "Mountain Landscape Sunset",
            "id": "ov-1",
            # Real Openverse shape (confirmed live): a bare license slug
            # plus a *separate* version field, not "cc0 1.0" pre-combined.
            "license": "cc0",
            "license_version": "1.0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "url": "https://example.com/openverse/full.jpg",
            "thumbnail": "https://example.com/openverse/thumb.jpg",
            "foreign_landing_url": "https://example.com/openverse/page",
            "width": 1920,
            "height": 1080,
            "creator": "Jane Doe",
        }
    ]
}

_WIKIMEDIA_PAYLOAD = {
    "query": {
        "pages": {
            "1": {
                "pageid": 1,
                "title": "File:Mountain_Landscape_Sunset.jpg",
                "imageinfo": [
                    {
                        "url": "https://example.com/wikimedia/full.jpg",
                        "descriptionurl": "https://example.com/wikimedia/page",
                        "thumburl": "https://example.com/wikimedia/thumb.jpg",
                        "width": 2048,
                        "height": 1365,
                        "extmetadata": {
                            "LicenseShortName": {"value": "CC BY-SA 4.0"},
                            "LicenseUrl": {
                                "value": "https://creativecommons.org/licenses/by-sa/4.0"
                            },
                            "Artist": {"value": "John Smith"},
                        },
                    }
                ],
            }
        }
    }
}

_EMPTY_WIKIMEDIA_PAYLOAD: dict[str, Any] = {"query": {"pages": {}}}
_EMPTY_OPENVERSE_PAYLOAD: dict[str, Any] = {"results": []}


class _FakeJsonResponse:
    def __init__(self, payload: dict[str, Any], *, status: int = 200):
        self._payload = payload
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            request = httpx.Request("GET", "https://example.com")
            response = httpx.Response(self._status, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeSearchClient:
    """Routes `.get(url, ...)` by URL substring to per-provider payloads."""

    def __init__(
        self,
        *,
        openverse: dict[str, Any] | None = None,
        wikimedia: dict[str, Any] | None = None,
        openverse_error: bool = False,
        **kwargs: Any,
    ):
        self._openverse = openverse if openverse is not None else _EMPTY_OPENVERSE_PAYLOAD
        self._wikimedia = wikimedia if wikimedia is not None else _EMPTY_WIKIMEDIA_PAYLOAD
        self._openverse_error = openverse_error
        self.calls: list[str] = []

    def __enter__(self) -> "_FakeSearchClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def get(self, url: str, **kwargs: Any) -> _FakeJsonResponse:
        self.calls.append(url)
        if "openverse" in url:
            return _FakeJsonResponse(self._openverse, status=502 if self._openverse_error else 200)
        return _FakeJsonResponse(self._wikimedia)


def test_search_licensed_images_merges_and_ranks_both_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient(openverse=_OPENVERSE_PAYLOAD, wikimedia=_WIKIMEDIA_PAYLOAD)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake_client)

    results = search_licensed_images("mountain landscape sunset")

    assert len(results) == 2
    providers = {result["provider"] for result in results}
    assert providers == {"openverse", "wikimedia"}
    no_attribution = next(r for r in results if r["provider"] == "openverse")
    assert no_attribution["license_tier"] == "no-attribution"
    assert no_attribution["attribution_text"] == ""
    assert no_attribution["license_name"] == "CC0"
    attribution_required = next(r for r in results if r["provider"] == "wikimedia")
    assert attribution_required["license_tier"] == "attribution-required"
    assert "John Smith" in attribution_required["attribution_text"]
    # no-attribution outranks attribution-required, all else roughly equal.
    assert results[0]["provider"] == "openverse"


def test_search_licensed_images_normalizes_openverse_bare_license_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    by_payload = {
        "results": [
            {
                "title": "Mountain Landscape Sunset",
                "id": "ov-2",
                "license": "by",
                "license_version": "3.0",
                "license_url": "https://creativecommons.org/licenses/by/3.0/",
                "url": "https://example.com/openverse/by-full.jpg",
                "creator": "Jane Doe",
                "width": 1920,
                "height": 1080,
            }
        ]
    }
    fake_client = _FakeSearchClient(openverse=by_payload)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake_client)

    results = search_licensed_images("mountain landscape sunset")

    assert len(results) == 1
    assert results[0]["license_name"] == "CC BY 3.0"
    assert results[0]["license_tier"] == "attribution-required"


def test_search_licensed_images_respects_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_client = _FakeSearchClient(openverse=_OPENVERSE_PAYLOAD, wikimedia=_WIKIMEDIA_PAYLOAD)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake_client)

    results = search_licensed_images("mountain landscape sunset", max_results=1)

    assert len(results) == 1


def test_search_licensed_images_deduplicates_identical_download_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    duplicate_wikimedia = {
        "query": {
            "pages": {
                "1": {
                    "pageid": 1,
                    "title": "File:Mountain_Landscape_Sunset.jpg",
                    "imageinfo": [
                        {
                            # Same URL as the Openverse candidate, differing
                            # only by a query string -- must still dedupe.
                            "url": "https://example.com/openverse/full.jpg?w=100",
                            "descriptionurl": "https://example.com/wikimedia/page",
                            "width": 1920,
                            "height": 1080,
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC0"},
                            },
                        }
                    ],
                }
            }
        }
    }
    fake_client = _FakeSearchClient(openverse=_OPENVERSE_PAYLOAD, wikimedia=duplicate_wikimedia)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake_client)

    results = search_licensed_images("mountain landscape sunset")

    assert len(results) == 1


def test_search_licensed_images_continues_when_one_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient(
        openverse=_OPENVERSE_PAYLOAD, wikimedia=_WIKIMEDIA_PAYLOAD, openverse_error=True
    )
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake_client)

    results = search_licensed_images("mountain landscape sunset")

    assert len(results) == 1
    assert results[0]["provider"] == "wikimedia"


def test_search_licensed_images_parses_comma_separated_required_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient(openverse=_OPENVERSE_PAYLOAD, wikimedia=_WIKIMEDIA_PAYLOAD)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake_client)

    # Both terms are satisfied by the fixture titles -- confirms the
    # comma-separated string is actually split into separate AND'd terms,
    # not treated as one long literal substring.
    results = search_licensed_images("mountain landscape sunset", required_terms="mountain, sunset")

    assert len(results) == 2


def test_search_licensed_images_returns_empty_list_when_nothing_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient()
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake_client)

    results = search_licensed_images("mountain landscape sunset")

    assert results == []


def test_search_licensed_images_rejects_candidates_missing_required_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = _FakeSearchClient(openverse=_OPENVERSE_PAYLOAD, wikimedia=_WIKIMEDIA_PAYLOAD)
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: fake_client)

    results = search_licensed_images("mountain landscape sunset", required_terms="eiffel tower")

    assert results == []

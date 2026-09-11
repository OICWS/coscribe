import io
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools import images as images_module
from coscribe.tools.images import ImageToolkit, build_image_tools, search_images


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

    download_metadata = get_tool_metadata(tools["download_image"])
    assert download_metadata.risk_category == "EXTERNAL"
    assert download_metadata.category == "web"
    assert download_metadata.requires_approval is True

"""Web image search + download, scoped to a single workspace root.

``search_images`` is read-only grounding for `write_pptx`/
`set_pptx_background_image`, the same way ``websearch.py``'s ``web_search``
is grounding for everything else -- it only returns candidate URLs,
nothing is fetched or written until ``download_image`` is called on one of
them.

Unlike ``.text()``'s multi-engine ``"auto"`` fallback (see
``websearch.py``'s own docstring), ``ddgs``'s ``.images()`` has exactly one
backend (it proxies Bing's image index through DuckDuckGo's own endpoint) --
a soft block on that one path fails the whole search, there's no fallback
engine to retry through. That's a real, accepted limitation, documented in
``ARCHITECTURE.md`` rather than papered over with a fake retry loop.

**Images found this way are not license-filtered.** ``search_images``
returns whatever DuckDuckGo's image index surfaces -- there is no "free to
use" / "commercial use OK" check anywhere in this pipeline (unlike a
curated stock-photo API such as Unsplash/Pexels, which was considered and
explicitly not chosen for this feature). A deck built with a
``download_image``-sourced picture should be treated as a draft, not
something safe to publish or send externally without the user separately
checking the image's actual rights -- ``coordinator.py``'s instructions
tell the model to say so, not silently treat it as clear.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ddgs import DDGS

from ..runtime.types import tool_metadata
from ._workspace import WorkspaceScope

# See websearch.py's own comment on the same subject: `from ddgs import
# DDGS` is `ddgs`'s lazy `_DDGSProxy`, so tests must monkeypatch
# `ddgs.ddgs.DDGS.images`, not `coscribe.tools.images.DDGS.images`.

_DEFAULT_MAX_RESULTS = 5
_DOWNLOAD_TIMEOUT = 15.0
_MAX_IMAGE_BYTES = 20_000_000  # 20MB -- a background/decorative image has
# no business being larger than this; a bigger response is almost always
# either the wrong content type or someone's multi-hundred-MB raw photo.
_CHUNK_SIZE = 65_536
# Several real image hosts (confirmed live: Wikimedia Commons) return 403
# for httpx's default "python-httpx/x.y" User-Agent -- a bare, honest
# browser-shaped UA string, not spoofing anything about origin/referrer.
_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; coscribe/1.0; "
        "+https://github.com/OICWS/project) coscribe-image-download"
    )
}


def search_images(query: str, max_results: int = _DEFAULT_MAX_RESULTS) -> list[dict[str, object]]:
    """Search the live web for images -- candidate backgrounds/decorative
    pictures for a deck, or anything else that needs a real photo/graphic
    rather than a hand-drawn shape. Returns candidate URLs only; nothing is
    downloaded until you call download_image on one of them.

    Results are NOT filtered by license -- see download_image's docstring.

    Args:
        query: what to search for, e.g. "mountain landscape sunset"
        max_results: how many results to return (default 5)
    """
    results = DDGS().images(query, max_results=max_results)
    return [
        {
            "title": str(result.get("title", "")),
            "image_url": str(result.get("image", "")),
            "thumbnail_url": str(result.get("thumbnail", "")),
            "source_page_url": str(result.get("url", "")),
            "width": result.get("width", ""),
            "height": result.get("height", ""),
        }
        for result in results
    ]


class ImageToolkit:
    def __init__(
        self,
        root: str | Path,
        *,
        extra_readable: Sequence[str | Path] = (),
        extra_writable: Sequence[str | Path] = (),
    ) -> None:
        self._scope = WorkspaceScope(
            root, extra_readable=extra_readable, extra_writable=extra_writable
        )

    def download_image(self, url: str, path: str, overwrite: bool = True) -> dict[str, object]:
        import httpx
        from PIL import Image

        scheme = urlsplit(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValueError(f"Only http/https URLs are supported, got scheme {scheme!r}: {url}")

        file_path = self._scope.resolve(path, write=True)
        if file_path.exists() and file_path.is_dir():
            raise ValueError(f"Path is a directory: {path}")
        if file_path.exists() and not overwrite:
            raise FileExistsError(f"File already exists: {path}")

        buffer = io.BytesIO()
        content_type = ""
        with httpx.Client(
            timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True, headers=_DOWNLOAD_HEADERS
        ) as client:
            with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                for chunk in response.iter_bytes(_CHUNK_SIZE):
                    buffer.write(chunk)
                    if buffer.tell() > _MAX_IMAGE_BYTES:
                        raise ValueError(
                            f"Image at {url} exceeds the {_MAX_IMAGE_BYTES // 1_000_000}MB limit"
                        )

        data = buffer.getvalue()
        # A Content-Type header alone isn't trustworthy (missing, wrong, or
        # an HTML error/login page served with a 200) -- Pillow actually
        # decoding the bytes is the real validation. verify() then a fresh
        # re-open, since Image.verify() leaves the file object unusable
        # for anything else afterward (documented Pillow behavior).
        try:
            Image.open(io.BytesIO(data)).verify()
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
        except Exception as exc:
            raise ValueError(f"URL did not return a valid image: {url}") from exc

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)

        return {
            "path": self._scope.relative(file_path),
            "bytes_written": len(data),
            "width": width,
            "height": height,
            "content_type": content_type,
        }


def build_image_tools(
    root: str | Path,
    *,
    extra_readable: Sequence[str | Path] = (),
    extra_writable: Sequence[str | Path] = (),
) -> list[Callable[..., Any]]:
    """Return the tool callables the Coordinator agent can call, bound to `root`
    (plus any user-configured extra_readable/extra_writable directories)."""
    toolkit = ImageToolkit(root, extra_readable=extra_readable, extra_writable=extra_writable)

    def download_image(url: str, path: str, overwrite: bool = True) -> dict[str, object]:
        """Download an image from a URL (e.g. one returned by search_images)
        into the workspace. Validates the response is actually a decodable
        image (rejects HTML error pages, non-image content, oversized
        responses) before writing anything.

        Args:
            url: the image's direct URL (http/https only)
            path: where to save it, relative to the workspace root
            overwrite: whether to replace the file if it already exists
        """
        return toolkit.download_image(url=url, path=path, overwrite=overwrite)

    return [
        tool_metadata(search_images, risk_category="READ", category="web"),
        tool_metadata(download_image, risk_category="EXTERNAL", category="web"),
    ]

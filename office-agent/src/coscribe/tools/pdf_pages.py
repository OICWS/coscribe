"""A PDF's pages as PNG images, for previewing an attached file before it is
sent. PDFium (pypdfium2) renders them, so it works the same on every system
without poppler or LibreOffice."""

from __future__ import annotations

import io
from pathlib import Path

from .documents import _PDFIUM_LOCK

MAX_WIDTH = 1600


def pdf_page_count(path: Path) -> int:
    import pypdfium2 as pdfium

    with _PDFIUM_LOCK:
        document = pdfium.PdfDocument(str(path))
        try:
            return len(document)
        finally:
            document.close()


def render_pdf_page(path: Path, page: int, width: int) -> bytes:
    """Page `page` (1-based) scaled to `width` pixels across, as PNG."""
    import pypdfium2 as pdfium

    width = max(1, min(width, MAX_WIDTH))
    with _PDFIUM_LOCK:
        document = pdfium.PdfDocument(str(path))
        try:
            if not 1 <= page <= len(document):
                raise ValueError(f"The file has {len(document)} pages.")
            pdf_page = document[page - 1]
            scale = width / pdf_page.get_width()
            image = pdf_page.render(scale=scale).to_pil()
        finally:
            document.close()
    out = io.BytesIO()
    image.convert("RGB").save(out, format="PNG")
    return out.getvalue()

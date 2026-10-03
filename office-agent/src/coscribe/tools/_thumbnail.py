"""Best-effort office-file thumbnail rendering via headless LibreOffice,
shared by documents.py/spreadsheets.py/presentations.py's write_* tools,
and (render_single_page_preview specifically) by web/session.py's
approval-preview (_build_pptx_edit_preview) -- a before/after slide
render shown in the approval card for a pptx edit, instead of just its
raw JSON arguments.

Same graceful-degradation contract as presentations.py's pre-existing
overflow-check pipeline: `soffice` missing, timing out, or failing to
convert is never an error -- the write has already succeeded by the time
this runs, a thumbnail is a diagnostic/preview extra on top of it, not a
requirement.

Previews are written under `<state_dir>/previews/`, not the user's
workspace -- they're generated bookkeeping, same category as
`state_dir`'s existing task storage, not a file the user
asked for. Naming them with a fresh uuid4 (never derived from the
source path) means `GET /api/previews/{name}` can look one up by a plain
filename match against that one directory without needing
WorkspaceScope-style path-traversal checking -- the name space is
entirely server-generated.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from ._office_bins import find_soffice

# The first launch on a fresh Windows profile builds LibreOffice's user
# profile and can take half a minute; killing it at 20s left every later
# call starting from scratch too.
SOFFICE_TIMEOUT = 60.0

_PREVIEW_DPI = 100


def _convert_to_pdf(file_path: Path, out_dir: Path) -> Path | None:
    """Shared soffice-to-PDF step behind render_all_page_previews and
    render_single_page_preview below -- factored out once a second caller
    needed the exact same subprocess call. Returns the produced PDF path,
    or None if soffice is missing, times out, or fails; never raises."""
    soffice = find_soffice()
    if soffice is None:
        return None
    try:
        subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(out_dir),
                str(file_path),
            ],
            capture_output=True,
            timeout=SOFFICE_TIMEOUT,
            check=True,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
        return None
    pdf_path = out_dir / f"{file_path.stem}.pdf"
    return pdf_path if pdf_path.is_file() else None


def render_thumbnail(file_path: Path, state_dir: Path | None) -> tuple[str | None, str | None]:
    """Render `file_path` (docx/pdf/xlsx/pptx) to a one-page PNG thumbnail
    under `state_dir/previews/`.

    Returns `(preview_name, None)` on success -- `preview_name` is a bare
    filename, not a path, suitable for `GET /api/previews/{preview_name}`.
    Returns `(None, reason)` if skipped for any reason; never raises.
    """
    if state_dir is None:
        return None, "no state_dir configured"
    soffice = find_soffice()
    if soffice is None:
        return None, "LibreOffice (soffice) not found"
    out_dir = Path(tempfile.mkdtemp(prefix="coscribe_thumbnail_"))
    try:
        try:
            subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    "png",
                    "--outdir",
                    str(out_dir),
                    str(file_path),
                ],
                capture_output=True,
                timeout=SOFFICE_TIMEOUT,
                check=True,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
            return None, "LibreOffice conversion failed"
        png_path = out_dir / f"{file_path.stem}.png"
        if not png_path.is_file():
            return None, "LibreOffice did not produce a PNG"
        previews_dir = state_dir / "previews"
        previews_dir.mkdir(parents=True, exist_ok=True)
        preview_name = f"{uuid.uuid4().hex}.png"
        shutil.move(str(png_path), str(previews_dir / preview_name))
        return preview_name, None
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def measure_page_words(pdf_path: Path, max_pages: int) -> list[dict[str, Any]]:
    """Per page: {"width", "height", "words": [(x0, y0, x1, y1)]} in points
    with y measured down from the top, from PDFium's character boxes -- where
    the text really landed once rendered, which the file alone can't say
    (inherited sizes, autofit). The loose box is the font's own ascent to
    descent, about 1.2x the size, the same box poppler's word boxes gave.
    Empty on any failure: a measurement is never worth failing a preview."""
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(str(pdf_path))
    except Exception:  # noqa: BLE001 -- see docstring
        return []
    pages: list[dict[str, Any]] = []
    try:
        for index in range(min(max_pages, len(document))):
            page = document[index]
            width, height = page.get_size()
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            words: list[tuple[float, float, float, float]] = []
            current: list[float] | None = None
            for position, char in enumerate(text):
                if char.isspace() or char in "\x02\ufffe":
                    if current is not None:
                        words.append((current[0], current[1], current[2], current[3]))
                        current = None
                    continue
                left, bottom, right, top = textpage.get_charbox(position, loose=True)
                box = [left, height - top, right, height - bottom]
                if current is None:
                    current = box
                else:
                    current = [
                        min(current[0], box[0]),
                        min(current[1], box[1]),
                        max(current[2], box[2]),
                        max(current[3], box[3]),
                    ]
            if current is not None:
                words.append((current[0], current[1], current[2], current[3]))
            pages.append({"width": float(width), "height": float(height), "words": words})
    except Exception:  # noqa: BLE001 -- see docstring
        return []
    finally:
        document.close()
    return pages


def _render_pdf_pages(pdf_path: Path, out_dir: Path, first: int, last: int) -> list[Path]:
    """PNG files for pages first..last (1-based, clipped to the document).
    PDFium renders them, so nothing outside Python is needed -- poppler is
    not something a Windows machine has."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(pdf_path))
    files: list[Path] = []
    try:
        for number in range(max(first, 1), min(last, len(document)) + 1):
            image = document[number - 1].render(scale=_PREVIEW_DPI / 72).to_pil()
            target = out_dir / f"page-{number:04d}.png"
            image.save(target)
            files.append(target)
    finally:
        document.close()
    return files


def render_all_page_previews(
    file_path: Path,
    state_dir: Path | None,
    max_pages: int = 8,
    layout: list[dict[str, Any]] | None = None,
) -> tuple[list[str], str | None]:
    """Render every page of `file_path` (docx/pdf/xlsx/pptx) to its own PNG
    under `state_dir/previews/`, via soffice-to-PDF (like `render_thumbnail`
    above) then PDFium to rasterize each page --
    `render_thumbnail`'s single PNG only ever shows page/slide 1, since
    soffice's own `--convert-to png` CLI path emits one page no matter how
    many the source has. That's not enough to catch a real, reproduced bug:
    a 4-slide deck where slide 1 (the title) looked fine and slides 2-4 were
    plain, undesigned bullet lists -- a first-slide-only check would have
    missed it entirely. This is what `run_node_script`-produced decks need
    for any real visual QA, since (unlike `write_pptx`) they get no
    preview_path of their own.

    Returns `(preview_names, None)` on success, capped at `max_pages` (a
    large deck's every slide isn't worth the token cost of reviewing each
    one). Returns `([], reason)` if skipped for any reason (soffice
    missing, conversion failure, timeout); never raises.
    """
    if state_dir is None:
        return [], "no state_dir configured"
    if find_soffice() is None:
        return [], "LibreOffice (soffice) not found"
    out_dir = Path(tempfile.mkdtemp(prefix="coscribe_thumbnail_"))
    try:
        pdf_path = _convert_to_pdf(file_path, out_dir)
        if pdf_path is None:
            return [], "LibreOffice conversion failed"
        if layout is not None:
            layout.extend(measure_page_words(pdf_path, max_pages))
        try:
            page_files = _render_pdf_pages(pdf_path, out_dir, 1, max_pages)
        except Exception:  # noqa: BLE001 -- a preview is never worth failing the write
            return [], "page rendering failed"
        if not page_files:
            return [], "the document has no pages to render"
        previews_dir = state_dir / "previews"
        previews_dir.mkdir(parents=True, exist_ok=True)
        preview_names = []
        for page_file in page_files:
            preview_name = f"{uuid.uuid4().hex}.png"
            shutil.move(str(page_file), str(previews_dir / preview_name))
            preview_names.append(preview_name)
        return preview_names, None
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)


def render_single_page_preview(
    file_path: Path, state_dir: Path | None, page: int
) -> tuple[str | None, str | None]:
    """Render just page/slide `page` (1-based) of `file_path` to a PNG
    under `state_dir/previews/` -- `render_all_page_previews` always
    starts from page 1 and is capped by *count*, not by which page, so
    it can't cheaply target one specific page deep into a large deck.
    web/session.py's approval-preview (`_build_pptx_edit_preview`) needs
    exactly that: the one slide a specific edit targets, wherever it
    falls in the deck, without paying to rasterize every slide before it.
    Same graceful-degradation contract as the rest of this module: never
    raises, `(None, reason)` on any failure (including `page` being out
    of range for this document)."""
    if state_dir is None:
        return None, "no state_dir configured"
    out_dir = Path(tempfile.mkdtemp(prefix="coscribe_thumbnail_"))
    try:
        pdf_path = _convert_to_pdf(file_path, out_dir)
        if pdf_path is None:
            return None, "LibreOffice conversion failed"
        try:
            page_files = _render_pdf_pages(pdf_path, out_dir, page, page)
        except Exception:  # noqa: BLE001 -- a preview is never worth failing the write
            return None, "page rendering failed"
        if not page_files:
            return None, f"page {page} out of range for this document"
        previews_dir = state_dir / "previews"
        previews_dir.mkdir(parents=True, exist_ok=True)
        preview_name = f"{uuid.uuid4().hex}.png"
        shutil.move(str(page_files[0]), str(previews_dir / preview_name))
        return preview_name, None
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

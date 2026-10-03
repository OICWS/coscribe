"""Finding LibreOffice off PATH, and measuring/rendering pages with PDFium."""

from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from coscribe.tools import _office_bins
from coscribe.tools._thumbnail import _render_pdf_pages, measure_page_words


def test_find_soffice_prefers_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_office_bins.shutil, "which", lambda name: "/opt/lo/soffice")

    assert _office_bins.find_soffice() == "/opt/lo/soffice"


def test_find_soffice_looks_in_the_windows_install_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    program = tmp_path / "Program Files" / "LibreOffice" / "program"
    program.mkdir(parents=True)
    (program / "soffice.exe").write_bytes(b"")
    monkeypatch.setattr(_office_bins.shutil, "which", lambda name: None)
    monkeypatch.setattr(_office_bins.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "Program Files"))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert _office_bins.find_soffice() == str(program / "soffice.exe")


def test_find_soffice_is_none_when_nothing_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_office_bins.shutil, "which", lambda name: None)
    monkeypatch.setattr(_office_bins.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramW6432", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    assert _office_bins.find_soffice() is None


def _two_page_pdf(path: Path) -> None:
    page = canvas.Canvas(str(path), pagesize=(600, 400))
    page.setFont("Helvetica", 20)
    page.drawString(50, 350, "Hello world")
    page.showPage()
    page.setFont("Helvetica", 10)
    page.drawString(50, 50, "second page")
    page.save()


def test_measure_page_words_reports_boxes_measured_down_from_the_top(tmp_path: Path) -> None:
    pdf = tmp_path / "two.pdf"
    _two_page_pdf(pdf)

    pages = measure_page_words(pdf, max_pages=8)

    assert [(p["width"], p["height"]) for p in pages] == [(600.0, 400.0), (600.0, 400.0)]
    first = pages[0]["words"]
    assert len(first) == 2  # "Hello", "world"
    x0, y0, x1, y1 = first[0]
    assert 40 < x0 < 60 and x1 > x0
    # Drawn near the top of the page: y grows downward, so it is small.
    assert y0 < 80
    # The loose box is about 1.2x the 20 pt font.
    assert 20 <= y1 - y0 <= 30
    assert len(pages[1]["words"]) == 2
    assert pages[1]["words"][0][1] > 300  # near the bottom


def test_measure_page_words_caps_pages_and_survives_a_broken_file(tmp_path: Path) -> None:
    pdf = tmp_path / "two.pdf"
    _two_page_pdf(pdf)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"not a pdf")

    assert len(measure_page_words(pdf, max_pages=1)) == 1
    assert measure_page_words(broken, max_pages=8) == []


def test_render_pdf_pages_makes_one_png_per_page_and_clips_the_range(tmp_path: Path) -> None:
    pdf = tmp_path / "two.pdf"
    _two_page_pdf(pdf)
    out = tmp_path / "out"
    out.mkdir()

    files = _render_pdf_pages(pdf, out, 1, 8)

    assert [f.name for f in files] == ["page-0001.png", "page-0002.png"]
    assert all(f.read_bytes().startswith(b"\x89PNG") for f in files)
    assert _render_pdf_pages(pdf, out, 2, 2)[0].name == "page-0002.png"
    assert _render_pdf_pages(pdf, out, 5, 5) == []

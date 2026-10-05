"""Finding LibreOffice off PATH, and measuring/rendering pages with PDFium."""

from __future__ import annotations

import shutil
import sys
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


def test_concurrent_soffice_runs_get_their_own_profiles_and_reuse_them() -> None:
    from coscribe.tools._office_bins import soffice_profile

    with soffice_profile() as first, soffice_profile() as second:
        assert first.startswith("-env:UserInstallation=file:")
        assert first != second
    with soffice_profile() as again:
        assert again == first


def test_a_profile_held_by_another_process_is_not_handed_out() -> None:
    """The live run and the test suite both took slot0 -- the claim on a
    profile has to hold across processes, not only threads."""
    import subprocess

    from coscribe.tools._office_bins import soffice_profile

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\n"
            "from coscribe.tools._office_bins import soffice_profile\n"
            "with soffice_profile() as p:\n"
            "    print(p, flush=True)\n"
            "    sys.stdin.readline()\n",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        held = holder.stdout.readline().strip()  # type: ignore[union-attr]
        with soffice_profile() as mine:
            assert held.startswith("-env:UserInstallation=")
            assert mine != held
    finally:
        holder.communicate("done\n", timeout=10)


@pytest.mark.real_libreoffice
@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice not installed")
def test_parallel_previews_all_render(tmp_path: Path) -> None:
    """Two of four parallel conversions on LibreOffice's default profile came
    back with nothing -- the deck and report checks of a parallel tool call
    were silently skipped."""
    from concurrent.futures import ThreadPoolExecutor

    from coscribe.tools._thumbnail import render_thumbnail
    from coscribe.tools.documents import DocumentToolkit

    toolkit = DocumentToolkit(tmp_path, state_dir=tmp_path / "state")
    for index in range(4):
        toolkit.write_pdf(path=f"p{index}.pdf", content=f"# Page {index}")
    with ThreadPoolExecutor(4) as pool:
        results = list(
            pool.map(
                lambda i: render_thumbnail(tmp_path / f"p{i}.pdf", tmp_path / "state"), range(4)
            )
        )

    assert [reason for _, reason in results] == [None] * 4


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")
def test_run_soffice_timeout_kills_what_the_launcher_started(tmp_path: Path) -> None:
    """`soffice` is a launcher; after a plain subprocess timeout the
    soffice.bin it started kept running, holding its profile."""
    import subprocess
    import time

    from coscribe.tools._office_bins import run_soffice

    marker = tmp_path / "still-running"
    launcher = f"(sleep 2; touch {marker}) & wait"
    with pytest.raises(subprocess.TimeoutExpired):
        run_soffice(["sh", "-c", launcher], 0.5)
    time.sleep(2.5)

    assert not marker.exists()


def test_run_soffice_reports_a_failed_run() -> None:
    import subprocess

    from coscribe.tools._office_bins import run_soffice

    with pytest.raises(subprocess.CalledProcessError):
        run_soffice([sys.executable, "-c", "raise SystemExit(3)"], 10)
    assert run_soffice([sys.executable, "-c", "print('ok')"], 10).stdout.strip() == b"ok"

"""convert_office_file: legacy formats up to OOXML, and PDF export."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from docx import Document
from openpyxl import Workbook, load_workbook

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools import _office_bins
from coscribe.tools.convert import build_convert_tools


def _convert(root: Path):  # type: ignore[no-untyped-def]
    (tool,) = build_convert_tools(root, state_dir=root / "state")
    return tool


def _libreoffice_works() -> bool:
    soffice = _office_bins.find_soffice()
    if soffice is None:
        return False
    try:
        subprocess.run([soffice, "--headless", "--version"], capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return True


needs_libreoffice = pytest.mark.skipif(
    not _libreoffice_works(), reason="LibreOffice not installed/functional in this environment"
)


def test_the_tool_writes_files_so_it_is_gated() -> None:
    (tool,) = build_convert_tools(Path("."), state_dir=Path("."))
    metadata = get_tool_metadata(tool)

    assert metadata.risk_category == "WRITE_LOCAL"
    assert metadata.requires_approval is True


def test_unsupported_source_and_target_are_refused_with_the_options(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    Document().save(str(tmp_path / "a.docx"))
    convert = _convert(tmp_path)

    with pytest.raises(ValueError, match="isn't a type"):
        convert(path="notes.txt", to_format="pdf")
    with pytest.raises(ValueError, match="can become: pdf"):
        convert(path="a.docx", to_format="xlsx")
    with pytest.raises(ValueError, match="does not exist"):
        convert(path="missing.doc", to_format="docx")


def test_an_existing_output_is_never_replaced(tmp_path: Path) -> None:
    Document().save(str(tmp_path / "a.docx"))
    (tmp_path / "a.pdf").write_bytes(b"keep me")
    convert = _convert(tmp_path)

    with pytest.raises(FileExistsError, match="already exists"):
        convert(path="a.docx", to_format="pdf")
    assert (tmp_path / "a.pdf").read_bytes() == b"keep me"


def test_without_libreoffice_it_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Document().save(str(tmp_path / "a.docx"))
    monkeypatch.setattr("coscribe.tools.convert.find_soffice", lambda: None)

    with pytest.raises(ValueError, match="LibreOffice isn't installed"):
        _convert(tmp_path)(path="a.docx", to_format="pdf")


@needs_libreoffice
def test_a_legacy_xls_becomes_a_readable_xlsx(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(["姓名", "分数"])
    sheet.append(["甲", 90])
    workbook.save(tmp_path / "old.xlsx")
    soffice = _office_bins.find_soffice()
    assert soffice is not None
    subprocess.run(
        [soffice, "--headless", "--convert-to", "xls", "--outdir", str(tmp_path),
         str(tmp_path / "old.xlsx")],
        capture_output=True,
        check=True,
        timeout=120,
    )
    (tmp_path / "old.xlsx").unlink()

    result = _convert(tmp_path)(path="old.xls", to_format="xlsx")

    assert result["path"] == "old.xlsx"
    converted = load_workbook(tmp_path / "old.xlsx").active
    assert converted is not None
    rows = [[c.value for c in row] for row in converted.iter_rows()]
    assert rows == [["姓名", "分数"], ["甲", 90]]
    assert (tmp_path / "old.xls").exists()  # the original stays


@needs_libreoffice
def test_a_docx_exports_to_a_pdf_at_the_chosen_path(tmp_path: Path) -> None:
    document = Document()
    document.add_paragraph("合同编号 HT-2025-001")
    document.save(str(tmp_path / "a.docx"))

    result = _convert(tmp_path)(path="a.docx", to_format="pdf", output_path="out/合同.pdf")

    assert result["path"] == "out/合同.pdf"
    pdf = tmp_path / "out" / "合同.pdf"
    assert pdf.read_bytes().startswith(b"%PDF")

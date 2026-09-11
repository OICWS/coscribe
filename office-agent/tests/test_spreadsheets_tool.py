import concurrent.futures
import shutil
import tempfile
from pathlib import Path

import pytest
from openpyxl import load_workbook

from coscribe.tools.spreadsheets import SpreadsheetToolkit, build_spreadsheet_tools


def _tools_by_name(root: Path) -> dict[str, object]:
    return {tool.__name__: tool for tool in build_spreadsheet_tools(root)}  # type: ignore[attr-defined]


def _libreoffice_actually_works() -> bool:
    """Same reasoning as test_presentations_tool.py's identical helper --
    `shutil.which("soffice")` alone doesn't prove conversion actually
    works in this environment."""
    probe_dir = Path(tempfile.mkdtemp(prefix="coscribe_lo_probe_"))
    try:
        result = SpreadsheetToolkit(probe_dir, state_dir=probe_dir / "state").write_xlsx(
            path="probe.xlsx", content=TABLE_CONTENT, sheet_name="Scores"
        )
        return result["preview_skipped_reason"] is None
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


TABLE_CONTENT = """\
| name | score |
| --- | --- |
| Alice | 9 |
| Bob | 7 |
"""


def test_write_xlsx_then_read_xlsx_round_trips_table(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    text = tools["read_xlsx"](path="report.xlsx", sheet="Scores")

    assert "| name | score |" in text
    assert "| Alice | 9 |" in text
    assert "| Bob | 7 |" in text


def test_write_xlsx_concurrent_calls_to_same_file_do_not_corrupt_it(tmp_path: Path) -> None:
    """Regression test for a real, live-hit bug: LangGraph runs one
    AIMessage's tool_calls concurrently (each on its own worker thread,
    since write_xlsx is a plain sync function) -- asked to build a
    multi-sheet workbook, a real model proposed all its write_xlsx calls
    to the same file at once, and two of them raced on
    load_workbook()/Workbook.save(), corrupting the file (openpyxl raised
    "File is not a zip file" reading it back mid-write from another
    thread). Fixed via tools/_file_locks.py's per-path lock
    (@locked_by_path on write_xlsx); this drives the same race
    deliberately with a thread pool and asserts every sheet survives."""
    tools = _tools_by_name(tmp_path)
    sheet_names = [f"Sheet{i}" for i in range(8)]

    def write(name: str) -> dict[str, object]:
        return tools["write_xlsx"](  # type: ignore[operator]
            path="concurrent.xlsx",
            content=f"| name | value |\n| --- | --- |\n| {name} | 1 |",
            sheet_name=name,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(write, sheet_names))

    assert all("path" in result for result in results)
    workbook = load_workbook(str(tmp_path / "concurrent.xlsx"))
    assert set(workbook.sheetnames) == set(sheet_names)


def test_read_xlsx_without_sheet_renders_every_sheet_with_headings(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")
    tools["write_xlsx"](
        path="report.xlsx",
        content="| x |\n| --- |\n| 1 |",
        sheet_name="Raw",
    )

    text = tools["read_xlsx"](path="report.xlsx")

    assert "## Scores" in text
    assert "## Raw" in text
    assert "| Alice | 9 |" in text
    assert "| 1 |" in text


def test_read_xlsx_unknown_sheet_lists_real_sheet_names(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="Scores"):
        tools["read_xlsx"](path="report.xlsx", sheet="Nope")


def test_write_xlsx_with_new_sheet_name_appends_rather_than_overwrites_file(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    tools["write_xlsx"](
        path="report.xlsx",
        content="| x |\n| --- |\n| 1 |",
        sheet_name="Raw",
        overwrite=False,
    )

    workbook = load_workbook(tmp_path / "report.xlsx")
    assert set(workbook.sheetnames) == {"Scores", "Raw"}


def test_write_xlsx_existing_sheet_without_overwrite_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(FileExistsError, match="Scores"):
        tools["write_xlsx"](
            path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores", overwrite=False
        )


def test_write_xlsx_existing_sheet_with_overwrite_replaces_content(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    tools["write_xlsx"](
        path="report.xlsx",
        content="| name | score |\n| --- | --- |\n| Carol | 5 |",
        sheet_name="Scores",
        overwrite=True,
    )

    text = tools["read_xlsx"](path="report.xlsx", sheet="Scores")
    assert "Carol" in text
    assert "Alice" not in text


def test_write_xlsx_coerces_numeric_cells_to_real_numbers(tmp_path: Path) -> None:
    toolkit = SpreadsheetToolkit(tmp_path)
    toolkit.write_xlsx(path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    workbook = load_workbook(tmp_path / "report.xlsx")
    sheet = workbook["Scores"]
    score_cell = sheet.cell(row=2, column=2).value

    assert isinstance(score_cell, int)
    assert score_cell == 9


def test_write_xlsx_rejects_non_table_content(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="pipe-table"):
        tools["write_xlsx"](path="report.xlsx", content="not a table", sheet_name="Scores")


def test_write_xlsx_writes_formula_strings_as_real_formulas(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    tools["write_xlsx"](
        path="report.xlsx",
        content="| a | b | sum |\n| --- | --- | --- |\n| 1 | 2 | =SUM(A2:B2) |",
        sheet_name="Scores",
    )

    workbook = load_workbook(tmp_path / "report.xlsx")
    cell = workbook["Scores"]["C2"]
    assert cell.data_type == "f"
    assert cell.value == "=SUM(A2:B2)"


def test_write_xlsx_rejects_xlookup_formula(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="XLOOKUP"):
        tools["write_xlsx"](
            path="report.xlsx",
            content="| a |\n| --- |\n| =XLOOKUP(1,A:A,A:A) |",
            sheet_name="Scores",
        )


def test_write_xlsx_auto_prefixes_xlfn_functions(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    tools["write_xlsx"](
        path="report.xlsx",
        content='| a |\n| --- |\n| =CONCAT("x","y") |',
        sheet_name="Scores",
    )

    workbook = load_workbook(tmp_path / "report.xlsx")
    assert workbook["Scores"]["A2"].value == '=_xlfn.CONCAT("x","y")'


def test_write_xlsx_does_not_double_prefix_an_already_prefixed_function(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    tools["write_xlsx"](
        path="report.xlsx",
        content='| a |\n| --- |\n| =_xlfn.CONCAT("x","y") |',
        sheet_name="Scores",
    )

    workbook = load_workbook(tmp_path / "report.xlsx")
    assert workbook["Scores"]["A2"].value == '=_xlfn.CONCAT("x","y")'


def test_write_xlsx_without_formulas_skips_recalc(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    result = tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    assert result["recalc_status"] == "skipped"
    assert result["recalc_skipped_reason"] is None


@pytest.mark.real_libreoffice
@pytest.mark.skipif(
    not _libreoffice_actually_works(),
    reason="LibreOffice not installed or not functional in this environment",
)
def test_write_xlsx_recalculates_formulas_via_libreoffice(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    result = tools["write_xlsx"](
        path="report.xlsx",
        content="| a | b | sum |\n| --- | --- | --- |\n| 1 | 2 | =SUM(A2:B2) |",
        sheet_name="Scores",
    )

    assert result["recalc_status"] == "success"
    assert result["total_formulas"] == 1
    assert result["total_errors"] == 0

    text = tools["read_xlsx"](path="report.xlsx", sheet="Scores")
    assert "| 1 | 2 | 3 |" in text


@pytest.mark.real_libreoffice
@pytest.mark.skipif(
    not _libreoffice_actually_works(),
    reason="LibreOffice not installed or not functional in this environment",
)
def test_write_xlsx_recalc_reports_formula_errors(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    result = tools["write_xlsx"](
        path="report.xlsx",
        content="| a | b | ratio |\n| --- | --- | --- |\n| 1 | 0 | =A2/B2 |",
        sheet_name="Scores",
    )

    assert result["recalc_status"] == "errors_found"
    assert result["total_errors"] == 1
    assert result["formula_error_locations"]["#DIV/0!"] == ["Scores!C2"]


@pytest.mark.real_libreoffice
@pytest.mark.skipif(
    not _libreoffice_actually_works(),
    reason="LibreOffice not installed or not functional in this environment",
)
def test_recalc_xlsx_recalculates_an_existing_file(tmp_path: Path) -> None:
    from openpyxl import Workbook

    tools = _tools_by_name(tmp_path)
    # Write formulas directly with plain openpyxl (bypassing write_xlsx's
    # own auto-recalc entirely) so this test proves recalc_xlsx itself
    # does the recalculation, not write_xlsx's side effect.
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["a", "b", "sum"])
    sheet.append([3, 4, "=SUM(A2:B2)"])
    workbook.save(tmp_path / "report.xlsx")
    assert load_workbook(tmp_path / "report.xlsx", data_only=True)["Sheet"]["C2"].value is None

    result = tools["recalc_xlsx"](path="report.xlsx")

    assert result["status"] == "success"
    assert result["total_formulas"] == 1
    recalculated = load_workbook(tmp_path / "report.xlsx", data_only=True)
    assert recalculated["Sheet"]["C2"].value == 7


def test_recalc_xlsx_on_missing_file_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        tools["recalc_xlsx"](path="missing.xlsx")


def test_recalc_xlsx_is_medium_risk_and_requires_approval(tmp_path: Path) -> None:
    from coscribe.runtime.types import get_tool_metadata

    tools = _tools_by_name(tmp_path)
    metadata = get_tool_metadata(tools["recalc_xlsx"])
    assert metadata.risk_category == "WRITE_LOCAL"
    assert metadata.requires_approval is True


def test_format_xlsx_cells_applies_number_format_and_color(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    result = tools["format_xlsx_cells"](
        path="report.xlsx",
        sheet_name="Scores",
        cell_range="B2:B3",
        number_format="$#,##0",
        font_color="0000FF",
        bold=True,
    )

    assert result["cells_formatted"] == 2
    workbook = load_workbook(tmp_path / "report.xlsx")
    cell = workbook["Scores"]["B2"]
    assert cell.number_format == "$#,##0"
    assert cell.font.color.rgb == "000000FF"
    assert cell.font.bold is True


def test_format_xlsx_cells_single_cell(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    result = tools["format_xlsx_cells"](
        path="report.xlsx", sheet_name="Scores", cell_range="B2", number_format="0.0%"
    )

    assert result["cells_formatted"] == 1
    workbook = load_workbook(tmp_path / "report.xlsx")
    assert workbook["Scores"]["B2"].number_format == "0.0%"


def test_format_xlsx_cells_rejects_unknown_sheet(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="Nope"):
        tools["format_xlsx_cells"](
            path="report.xlsx", sheet_name="Nope", cell_range="B2", number_format="0.0%"
        )


def test_format_xlsx_cells_rejects_invalid_range(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="cell_range"):
        tools["format_xlsx_cells"](
            path="report.xlsx", sheet_name="Scores", cell_range="not a range", number_format="0.0%"
        )


def test_format_xlsx_cells_is_medium_risk_and_requires_approval(tmp_path: Path) -> None:
    from coscribe.runtime.types import get_tool_metadata

    tools = _tools_by_name(tmp_path)
    metadata = get_tool_metadata(tools["format_xlsx_cells"])
    assert metadata.risk_category == "WRITE_LOCAL"
    assert metadata.requires_approval is True


def test_write_xlsx_skips_preview_without_state_dir(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)  # no state_dir passed

    result = tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    assert result["preview_path"] is None
    assert result["preview_skipped_reason"] == "no state_dir configured"


@pytest.mark.real_libreoffice
@pytest.mark.skipif(
    not _libreoffice_actually_works(),
    reason="LibreOffice not installed or not functional in this environment",
)
def test_write_xlsx_generates_real_preview_via_libreoffice(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    tools = {
        tool.__name__: tool
        for tool in build_spreadsheet_tools(tmp_path / "workspace", state_dir=state_dir)  # type: ignore[attr-defined]
    }

    result = tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    assert result["preview_skipped_reason"] is None
    preview_path = state_dir / "previews" / result["preview_path"]
    assert preview_path.is_file()
    assert preview_path.stat().st_size > 0


def test_add_xlsx_chart_adds_a_chart_of_the_right_type(tmp_path: Path) -> None:
    from openpyxl.chart import BarChart

    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    tools["add_xlsx_chart"](
        path="report.xlsx",
        sheet_name="Scores",
        chart_type="bar",
        data_range="A1:B3",
        title="Scores by name",
    )

    workbook = load_workbook(tmp_path / "report.xlsx")
    charts = workbook["Scores"]._charts
    assert len(charts) == 1
    assert isinstance(charts[0], BarChart)


def test_add_xlsx_chart_defaults_to_the_sheets_whole_used_range(tmp_path: Path) -> None:
    # Regression test for a real, live-reproduced failure: a model asked to
    # name data_range itself guessed one row too low and one column too
    # wide, silently dropping the first data row and charting a phantom
    # empty series from a column that doesn't exist. Omitting data_range
    # must chart every row/column actually written, not guess.
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    tools["add_xlsx_chart"](path="report.xlsx", sheet_name="Scores", chart_type="bar")

    workbook = load_workbook(tmp_path / "report.xlsx")
    chart = workbook["Scores"]._charts[0]
    assert len(chart.series) == 1
    assert chart.series[0].cat.numRef.f == "'Scores'!$A$2:$A$3"
    assert chart.series[0].val.numRef.f == "'Scores'!$B$2:$B$3"


def test_add_xlsx_chart_rejects_unknown_chart_type(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="Unknown chart_type"):
        tools["add_xlsx_chart"](
            path="report.xlsx", sheet_name="Scores", chart_type="scatter", data_range="A1:B3"
        )


def test_add_xlsx_chart_rejects_unknown_sheet(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="Scores"):
        tools["add_xlsx_chart"](
            path="report.xlsx", sheet_name="Nope", chart_type="bar", data_range="A1:B3"
        )


def test_add_xlsx_chart_rejects_invalid_range(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="data_range"):
        tools["add_xlsx_chart"](
            path="report.xlsx", sheet_name="Scores", chart_type="bar", data_range="not a range"
        )


def test_add_xlsx_chart_rejects_multi_column_pie_chart(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](
        path="report.xlsx",
        content="| name | q1 | q2 |\n| --- | --- | --- |\n| Alice | 9 | 4 |",
        sheet_name="Scores",
    )

    with pytest.raises(ValueError, match="pie"):
        tools["add_xlsx_chart"](
            path="report.xlsx", sheet_name="Scores", chart_type="pie", data_range="A1:C2"
        )


def test_add_xlsx_chart_on_missing_file_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        tools["add_xlsx_chart"](
            path="missing.xlsx", sheet_name="Scores", chart_type="bar", data_range="A1:B3"
        )


def test_add_xlsx_chart_is_medium_risk_and_requires_approval(tmp_path: Path) -> None:
    from coscribe.runtime.types import get_tool_metadata

    tools = _tools_by_name(tmp_path)
    metadata = get_tool_metadata(tools["add_xlsx_chart"])
    assert metadata.risk_category == "WRITE_LOCAL"
    assert metadata.requires_approval is True


def test_extra_writable_dir_lets_write_xlsx_land_outside_workspace(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    workspace = tmp_path / "workspace"
    tools = {
        tool.__name__: tool
        for tool in build_spreadsheet_tools(workspace, extra_writable=[shared])  # type: ignore[attr-defined]
    }

    tools["write_xlsx"](
        path=str(shared / "report.xlsx"), content=TABLE_CONTENT, sheet_name="Scores"
    )

    text = tools["read_xlsx"](path=str(shared / "report.xlsx"), sheet="Scores")
    assert "| Alice | 9 |" in text


def test_extra_readable_dir_rejects_write_xlsx(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    workspace = tmp_path / "workspace"
    tools = {
        tool.__name__: tool
        for tool in build_spreadsheet_tools(workspace, extra_readable=[downloads])  # type: ignore[attr-defined]
    }

    with pytest.raises(PermissionError):
        tools["write_xlsx"](
            path=str(downloads / "report.xlsx"), content=TABLE_CONTENT, sheet_name="Scores"
        )

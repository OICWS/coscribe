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


def test_merge_xlsx_cells_merges_a_range(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    result = tools["merge_xlsx_cells"](path="report.xlsx", sheet_name="Scores", cell_range="A1:B1")
    assert result["cell_range"] == "A1:B1"

    workbook = load_workbook(tmp_path / "report.xlsx")
    ranges = {str(r) for r in workbook["Scores"].merged_cells.ranges}
    assert ranges == {"A1:B1"}


def test_merge_xlsx_cells_rejects_overlap_with_existing_merge(tmp_path: Path) -> None:
    # openpyxl's own merge_cells does not raise on this -- confirmed while
    # building this tool -- so the tool has to check it itself.
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")
    tools["merge_xlsx_cells"](path="report.xlsx", sheet_name="Scores", cell_range="A1:B1")

    with pytest.raises(ValueError, match="overlaps"):
        tools["merge_xlsx_cells"](path="report.xlsx", sheet_name="Scores", cell_range="B1:C2")


def test_merge_xlsx_cells_rejects_unknown_sheet(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="Nope"):
        tools["merge_xlsx_cells"](path="report.xlsx", sheet_name="Nope", cell_range="A1:B1")


def test_unmerge_xlsx_cells_restores_individual_cells(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")
    tools["merge_xlsx_cells"](path="report.xlsx", sheet_name="Scores", cell_range="A1:B1")

    tools["unmerge_xlsx_cells"](path="report.xlsx", sheet_name="Scores", cell_range="A1:B1")

    workbook = load_workbook(tmp_path / "report.xlsx")
    assert workbook["Scores"].merged_cells.ranges == set()


def test_unmerge_xlsx_cells_raises_when_not_merged(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="Cannot unmerge"):
        tools["unmerge_xlsx_cells"](path="report.xlsx", sheet_name="Scores", cell_range="A1:B1")


def test_add_xlsx_conditional_format_cell_is(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    result = tools["add_xlsx_conditional_format"](
        path="report.xlsx",
        sheet_name="Scores",
        cell_range="B2:B3",
        rule_type="cell_is",
        operator=">",
        formula="8",
        fill_color="FF0000",
    )
    assert result["rule_type"] == "cell_is"

    workbook = load_workbook(tmp_path / "report.xlsx")
    rules = workbook["Scores"].conditional_formatting["B2:B3"]
    assert len(rules) == 1
    assert rules[0].type == "cellIs"
    assert rules[0].operator == "greaterThan"


def test_add_xlsx_conditional_format_color_scale(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    tools["add_xlsx_conditional_format"](
        path="report.xlsx",
        sheet_name="Scores",
        cell_range="B2:B3",
        rule_type="color_scale",
        min_color="FF0000",
        max_color="00FF00",
    )

    workbook = load_workbook(tmp_path / "report.xlsx")
    rules = workbook["Scores"].conditional_formatting["B2:B3"]
    assert rules[0].type == "colorScale"


def test_add_xlsx_conditional_format_cell_is_requires_operator_and_formula(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="requires both operator and formula"):
        tools["add_xlsx_conditional_format"](
            path="report.xlsx", sheet_name="Scores", cell_range="B2:B3", rule_type="cell_is"
        )


def test_add_xlsx_conditional_format_rejects_unknown_rule_type(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="Unknown rule_type"):
        tools["add_xlsx_conditional_format"](
            path="report.xlsx", sheet_name="Scores", cell_range="B2:B3", rule_type="icon_set"
        )


def test_set_xlsx_data_validation_list_auto_quotes_a_plain_comma_list(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    tools["set_xlsx_data_validation"](
        path="report.xlsx",
        sheet_name="Scores",
        cell_range="A2:A3",
        validation_type="list",
        formula1="Alice,Bob,Carol",
    )

    workbook = load_workbook(tmp_path / "report.xlsx")
    validations = workbook["Scores"].data_validations.dataValidation
    assert len(validations) == 1
    assert validations[0].type == "list"
    assert validations[0].formula1 == '"Alice,Bob,Carol"'


def test_set_xlsx_data_validation_list_leaves_a_range_reference_alone(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    tools["set_xlsx_data_validation"](
        path="report.xlsx",
        sheet_name="Scores",
        cell_range="A2:A3",
        validation_type="list",
        formula1="$D$1:$D$5",
    )

    workbook = load_workbook(tmp_path / "report.xlsx")
    assert workbook["Scores"].data_validations.dataValidation[0].formula1 == "$D$1:$D$5"


def test_set_xlsx_data_validation_whole_number_range(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    tools["set_xlsx_data_validation"](
        path="report.xlsx",
        sheet_name="Scores",
        cell_range="B2:B3",
        validation_type="whole",
        formula1="0",
        formula2="100",
        operator="between",
        error_message="Enter a score from 0 to 100",
    )

    workbook = load_workbook(tmp_path / "report.xlsx")
    validation = workbook["Scores"].data_validations.dataValidation[0]
    assert validation.type == "whole"
    assert validation.operator == "between"
    assert validation.formula1 == "0"
    assert validation.formula2 == "100"
    assert validation.error == "Enter a score from 0 to 100"


def test_set_xlsx_data_validation_rejects_unknown_type(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="Unknown validation_type"):
        tools["set_xlsx_data_validation"](
            path="report.xlsx",
            sheet_name="Scores",
            cell_range="A2:A3",
            validation_type="bogus",
            formula1="1",
        )


def test_freeze_xlsx_panes_sets_and_clears(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    result = tools["freeze_xlsx_panes"](path="report.xlsx", sheet_name="Scores", cell="A2")
    assert result["freeze_panes"] == "A2"
    workbook = load_workbook(tmp_path / "report.xlsx")
    assert workbook["Scores"].freeze_panes == "A2"

    tools["freeze_xlsx_panes"](path="report.xlsx", sheet_name="Scores", cell="")
    workbook = load_workbook(tmp_path / "report.xlsx")
    assert workbook["Scores"].freeze_panes is None


def test_set_xlsx_column_width_single_and_range(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    result = tools["set_xlsx_column_width"](
        path="report.xlsx", sheet_name="Scores", columns="B:C", width=25.0
    )
    assert result["columns"] == ["B", "C"]

    workbook = load_workbook(tmp_path / "report.xlsx")
    assert workbook["Scores"].column_dimensions["B"].width == 25.0
    assert workbook["Scores"].column_dimensions["C"].width == 25.0


def test_set_xlsx_column_width_rejects_end_before_start(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="end is before start"):
        tools["set_xlsx_column_width"](
            path="report.xlsx", sheet_name="Scores", columns="C:A", width=10.0
        )


def test_edit_xlsx_cells_writes_only_the_addressed_block(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    result = tools["edit_xlsx_cells"](
        path="report.xlsx", sheet_name="Scores", start_cell="B2", content="| 42 |"
    )
    assert result["rows_written"] == 1
    assert result["cols_written"] == 1

    workbook = load_workbook(tmp_path / "report.xlsx", data_only=True)
    sheet = workbook["Scores"]
    assert sheet["B2"].value == 42
    # Everything else on the sheet survives untouched.
    assert sheet["A1"].value == "name"
    assert sheet["A2"].value == "Alice"
    assert sheet["A3"].value == "Bob"
    assert sheet["B3"].value == 7


def test_edit_xlsx_cells_preserves_existing_formatting_and_merges(tmp_path: Path) -> None:
    # The concrete claim this tool exists to back up: editing one cell
    # must not disturb structure write_xlsx's whole-sheet-replace would.
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")
    tools["merge_xlsx_cells"](path="report.xlsx", sheet_name="Scores", cell_range="A1:B1")
    tools["format_xlsx_cells"](
        path="report.xlsx", sheet_name="Scores", cell_range="B2:B3", font_color="0000FF"
    )

    tools["edit_xlsx_cells"](
        path="report.xlsx", sheet_name="Scores", start_cell="A3", content="| Carol |"
    )

    workbook = load_workbook(tmp_path / "report.xlsx")
    sheet = workbook["Scores"]
    assert {str(r) for r in sheet.merged_cells.ranges} == {"A1:B1"}
    assert sheet["B2"].font.color.rgb == "000000FF"
    assert sheet["A3"].value == "Carol"


def test_edit_xlsx_cells_writes_a_formula_and_recalculates(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    result = tools["edit_xlsx_cells"](
        path="report.xlsx", sheet_name="Scores", start_cell="C1", content="| =SUM(B2:B3) |"
    )
    assert result["recalc_status"] in ("success", "skipped")
    if result["recalc_status"] == "success":
        workbook = load_workbook(tmp_path / "report.xlsx", data_only=True)
        assert workbook["Scores"]["C1"].value == 16


def test_edit_xlsx_cells_rejects_unknown_sheet(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_xlsx"](path="report.xlsx", content=TABLE_CONTENT, sheet_name="Scores")

    with pytest.raises(ValueError, match="Nope"):
        tools["edit_xlsx_cells"](
            path="report.xlsx", sheet_name="Nope", start_cell="A1", content="| x |"
        )


def test_edit_xlsx_cells_on_missing_file_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        tools["edit_xlsx_cells"](
            path="missing.xlsx", sheet_name="Scores", start_cell="A1", content="| x |"
        )


def test_xlsx_gap_filling_tools_are_medium_risk_and_require_approval(tmp_path: Path) -> None:
    from coscribe.runtime.types import get_tool_metadata

    tools = _tools_by_name(tmp_path)
    for name in (
        "merge_xlsx_cells",
        "unmerge_xlsx_cells",
        "add_xlsx_conditional_format",
        "set_xlsx_data_validation",
        "freeze_xlsx_panes",
        "set_xlsx_column_width",
        "edit_xlsx_cells",
    ):
        metadata = get_tool_metadata(tools[name])
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

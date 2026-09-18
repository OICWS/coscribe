from pathlib import Path

from openpyxl import Workbook

from coscribe.tools._xlsx_lookup_formulas import (
    FormulaError,
    evaluate_and_patch_lookup_formulas,
    evaluate_formula,
    parse_single_call,
    xmatch_index,
)


def test_parse_single_call_recognizes_a_plain_call() -> None:
    assert parse_single_call('=XLOOKUP("Bob",A2:A3,B2:B3)') == (
        "XLOOKUP",
        ['"Bob"', "A2:A3", "B2:B3"],
    )


def test_parse_single_call_strips_xlfn_prefix_case_insensitively() -> None:
    assert parse_single_call('=_xlfn.xlookup("Bob",A2:A3,B2:B3)') == (
        "XLOOKUP",
        ['"Bob"', "A2:A3", "B2:B3"],
    )


def test_parse_single_call_rejects_a_non_top_level_call() -> None:
    assert parse_single_call('=1+XLOOKUP("Bob",A2:A3,B2:B3)') is None
    assert parse_single_call('=XLOOKUP("Bob",A2:A3,B2:B3)+1') is None


def test_parse_single_call_rejects_an_unsupported_function() -> None:
    assert parse_single_call("=SUM(A1:A3)") is None


def test_parse_single_call_splits_args_respecting_nested_parens_and_quotes() -> None:
    # A quoted string containing a comma, and a nested function call with
    # its own comma, must not fracture the top-level argument split.
    name, args = parse_single_call('=XLOOKUP("a,b",A:A,IF(B1>0,C:C,D:D))')  # type: ignore[misc]
    assert name == "XLOOKUP"
    assert args == ['"a,b"', "A:A", "IF(B1>0,C:C,D:D)"]


def test_xmatch_index_exact_match() -> None:
    assert xmatch_index("Bob", ["Alice", "Bob", "Carol"], match_mode=0, search_mode=1) == 1


def test_xmatch_index_is_case_insensitive_for_text() -> None:
    assert xmatch_index("bob", ["Alice", "Bob", "Carol"], match_mode=0, search_mode=1) == 1


def test_xmatch_index_number_and_its_string_form_do_not_match() -> None:
    from coscribe.tools._xlsx_lookup_formulas import _NO_MATCH

    assert xmatch_index(42, ["42"], match_mode=0, search_mode=1) is _NO_MATCH


def test_xmatch_index_approximate_next_smaller() -> None:
    # match_mode=-1: exact match or the largest value less than lookup_value.
    assert xmatch_index(5, [1, 3, 7, 9], match_mode=-1, search_mode=1) == 1  # value 3


def test_xmatch_index_approximate_next_larger() -> None:
    assert xmatch_index(5, [1, 3, 7, 9], match_mode=1, search_mode=1) == 2  # value 7


def test_xmatch_index_wildcard_match() -> None:
    assert xmatch_index("A*", ["Bob", "Apple", "Ant"], match_mode=2, search_mode=1) == 1


def test_xmatch_index_reverse_search_returns_the_last_match() -> None:
    assert xmatch_index("x", ["x", "y", "x"], match_mode=0, search_mode=-1) == 2


def _values_workbook(rows: list[list[object]]):
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    return wb


def test_evaluate_formula_xlookup_scalar() -> None:
    wb = _values_workbook([["Alice", 9], ["Bob", 7], ["Carol", 5]])
    result = evaluate_formula('=XLOOKUP("Bob",A1:A3,B1:B3)', "Sheet", wb)
    assert result == 7


def test_evaluate_formula_xlookup_with_fallback() -> None:
    wb = _values_workbook([["Alice", 9], ["Bob", 7]])
    result = evaluate_formula('=XLOOKUP("Nobody",A1:A2,B1:B2,"n/a")', "Sheet", wb)
    assert result == "n/a"


def test_evaluate_formula_xlookup_no_fallback_is_formula_error() -> None:
    wb = _values_workbook([["Alice", 9], ["Bob", 7]])
    result = evaluate_formula('=XLOOKUP("Nobody",A1:A2,B1:B2)', "Sheet", wb)
    assert isinstance(result, FormulaError)
    assert result.code == "#N/A"


def test_evaluate_formula_xlookup_mismatched_range_lengths_is_value_error() -> None:
    wb = _values_workbook([["Alice", 9], ["Bob", 7], ["Carol", 5]])
    result = evaluate_formula('=XLOOKUP("Bob",A1:A3,B1:B2)', "Sheet", wb)
    assert isinstance(result, FormulaError)
    assert result.code == "#VALUE!"


def test_evaluate_formula_xmatch() -> None:
    wb = _values_workbook([["Alice"], ["Bob"], ["Carol"]])
    result = evaluate_formula('=XMATCH("Carol",A1:A3)', "Sheet", wb)
    assert result == 3


def test_evaluate_formula_declines_a_non_reference_argument() -> None:
    # SUM(A1:A2) as an argument isn't a plain literal or reference this
    # module resolves -- must decline (not guess), same contract as a
    # too-complex whole formula.
    from coscribe.tools._xlsx_lookup_formulas import _UNRESOLVABLE

    wb = _values_workbook([["Alice", 9], ["Bob", 7]])
    result = evaluate_formula('=XLOOKUP(SUM(A1:A1),A1:A2,B1:B2)', "Sheet", wb)
    assert result is _UNRESOLVABLE


def test_evaluate_and_patch_lookup_formulas_end_to_end(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append(["Alice", 9])
    ws.append(["Bob", 7])
    ws["D1"] = '=_xlfn.XLOOKUP("Bob",A1:A2,B1:B2)'
    path = tmp_path / "t.xlsx"
    wb.save(str(path))

    report = evaluate_and_patch_lookup_formulas(path)

    assert report["formulas_evaluated"] == 1
    assert report["results"] == {"Sheet!D1": "7"}

    from openpyxl import load_workbook

    reopened = load_workbook(str(path), data_only=True)
    assert reopened.active["D1"].value == 7


def test_evaluate_and_patch_lookup_formulas_resolves_the_correct_sheet_in_a_multi_sheet_workbook(
    tmp_path: Path,
) -> None:
    # Real bug caught building this: openpyxl writes relationship Targets
    # as absolute-from-package-root ("/xl/worksheets/sheet1.xml"), not
    # relative -- a naive `target if target.startswith("xl/") else
    # f"xl/{target}"` check misses the leading "/" and produces a bogus
    # "xl//xl/worksheets/sheet1.xml" path that matches no real archive
    # entry, silently patching nothing. A single-sheet workbook can't
    # catch this (there's nothing to mismatch against); a second sheet
    # can and does.
    wb = Workbook()
    data_sheet = wb.active
    data_sheet.title = "Data"
    data_sheet.append(["Alice", 9])
    data_sheet.append(["Bob", 7])
    data_sheet["D1"] = '=_xlfn.XLOOKUP("Bob",A1:A2,B1:B2)'
    other_sheet = wb.create_sheet("Other")
    other_sheet["A1"] = "untouched"
    path = tmp_path / "t.xlsx"
    wb.save(str(path))

    report = evaluate_and_patch_lookup_formulas(path)

    assert report == {"formulas_evaluated": 1, "results": {"Data!D1": "7"}}

    from openpyxl import load_workbook

    reopened = load_workbook(str(path), data_only=True)
    assert reopened["Data"]["D1"].value == 7
    assert reopened["Other"]["A1"].value == "untouched"


def test_evaluate_and_patch_lookup_formulas_is_a_no_op_without_matches(tmp_path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.append([1, 2, "=A1+B1"])
    path = tmp_path / "t.xlsx"
    original_bytes = None
    wb.save(str(path))
    original_bytes = path.read_bytes()

    report = evaluate_and_patch_lookup_formulas(path)

    assert report == {"formulas_evaluated": 0, "results": {}}
    assert path.read_bytes() == original_bytes

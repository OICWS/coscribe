"""Excel (.xlsx) read/write tools, scoped to a single workspace root.

Mirrors ``tools/documents.py``'s docx/pdf tools -- same ``WorkspaceScope``
sandboxing, same reasoning for being a built-in tool rather than an MCP
server (``openpyxl`` is just as mature/audited as ``python-docx``/
``pdfplumber``, and there's no more authoritative Excel MCP server than the
personally-maintained niche repos already rejected for Word/PDF -- see
``ARCHITECTURE.md``).

Unlike docx/pdf, a workbook is a *grid*, not flowing text, so ``write_xlsx``
only accepts pipe-table rows (reusing ``documents.py``'s row-parsing
helpers), not the full heading/bullet/paragraph markdown subset. And
``overwrite`` here is scoped to *the named sheet*, not the whole file --
see ``write_xlsx``'s docstring -- since building up a multi-sheet workbook
across several calls is the one thing that meaningfully differs about
spreadsheets versus the one-shot documents in ``documents.py``.

``openpyxl`` is imported lazily, inside each method that actually needs
it, rather than at module level -- same real, measured startup-time
reasoning as ``documents.py``'s identical change, see that module's
docstring and runtime_lg/README.md's startup-time section. ``Worksheet``
is only ever used as a type annotation (never at runtime, thanks to
``from __future__ import annotations``), so it's imported under
``TYPE_CHECKING`` instead of even a lazy runtime import.

``add_xlsx_chart`` only builds through openpyxl's high-level chart
classes (``BarChart``/``LineChart``/``PieChart`` + ``Reference``), never
hand-written chart XML -- axis/series registration is correct by
construction that way. Deliberately scoped to single-purpose bar/line/pie
charts, no stacked or secondary-axis/combo charts: those are exactly the
configurations Anthropic's own pptx skill (referenced in
``ARCHITECTURE.md``) documents as producing schema-valid XML that Excel/
PowerPoint silently treats as corrupt (a stacked bar's data-label position,
a combo chart's under-declared secondary axis). Restricting the feature
surface avoids that failure class outright rather than needing a
validator to catch it after the fact.

``write_xlsx`` writes formula strings (``"=SUM(A2:B2)"``) as real Excel
formulas -- openpyxl already does this for any cell string starting with
``=``, no special handling needed there. What *is* handled explicitly,
cross-checked against Anthropic's own published xlsx skill rather than
guessed: (1) a hard block on ``XLOOKUP``/``XMATCH``/``SORT``/``FILTER``/
``UNIQUE``/``SEQUENCE`` -- LibreOffice (used below to verify every formula
actually evaluates) cannot compute these under any prefix, and because
openpyxl writes no spill metadata, a partially-working version would
silently populate only one cell instead of failing loudly; (2)
auto-prefixing the six post-2007 functions Excel stores with a hidden
``_xlfn.`` prefix (``TEXTJOIN``/``CONCAT``/``IFS``/``SWITCH``/``MAXIFS``/
``MINIFS``) -- written bare, each evaluates to ``#NAME?`` in the saved
file, so the model doesn't need to remember this Excel/OOXML wrinkle
itself. After writing, ``_recalc_xlsx`` force-recalculates the whole
workbook via a LibreOffice macro (the same ``ThisComponent.calculateAll()
+ store() + close()`` technique the xlsx skill's own ``recalc.py`` uses --
plain ``--convert-to`` does NOT force recalculation, only a macro does,
verified empirically here) and reports any Excel error strings
(``#DIV/0!`` etc.) found afterward. Without this, every formula cell
openpyxl just wrote reads back as ``None`` to ``read_xlsx``/anything using
``data_only=True`` until the user happens to open the file in real Excel
-- a real, confirmed gap this closes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from ..runtime.types import tool_metadata
from ._file_locks import locked_by_path
from ._thumbnail import render_thumbnail
from ._workspace import WorkspaceScope
from .documents import _is_separator_row, _split_table_row

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet

_XLSX_RECALC_TIMEOUT = 30.0

# Never usable under any prefix -- LibreOffice (used to verify formulas
# below) cannot evaluate these, and since openpyxl writes no spill
# metadata, a partial success would silently populate only the top-left
# cell rather than failing loudly. Confirmed against Anthropic's own
# published xlsx skill, not guessed.
_XLSX_UNSUPPORTED_FUNCTIONS = ("XLOOKUP", "XMATCH", "SORT", "FILTER", "UNIQUE", "SEQUENCE")

# Excel stores these six post-2007 functions with a hidden `_xlfn.` prefix
# in the file's XML (its UI hides the prefix) -- written bare, each
# evaluates to #NAME?. Confirmed against the same source.
_XLSX_XLFN_FUNCTIONS = ("TEXTJOIN", "CONCAT", "IFS", "SWITCH", "MAXIFS", "MINIFS")

_XLSX_ERROR_STRINGS = ("#VALUE!", "#DIV/0!", "#REF!", "#NAME?", "#NULL!", "#NUM!", "#N/A")

# ThisComponent.calculateAll() + store() + close() via a StarBasic macro is
# the only way that actually forces LibreOffice to recalculate every
# formula and write back cached values -- plain `soffice --convert-to`
# reuses whatever's already cached (nothing, for an openpyxl-written file).
# Same technique Anthropic's own xlsx skill's recalc.py uses. Verified
# empirically in this sandbox (see _recalc_xlsx's docstring): invoking the
# macro requires priming a throwaway LibreOffice profile first (an
# uninitialized one has no basic/Standard library for the macro to load
# into), then overwriting that profile's default Module1.xba.
_XLSX_RECALC_MACRO = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">
<script:module xmlns:script="http://openoffice.org/2000/script"
    script:name="Module1" script:language="StarBasic">
    Sub RecalculateAndSave()
      ThisComponent.calculateAll()
      ThisComponent.store()
      ThisComponent.close(True)
    End Sub
</script:module>"""


def _parse_table_rows(content: str) -> list[list[str]]:
    """Parse pipe-table-only markdown (no headings/bullets/paragraphs --
    a spreadsheet is a grid, not flowing text) into raw string rows."""
    rows: list[list[str]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cells = _split_table_row(line)
        if cells is None:
            raise ValueError(
                f"write_xlsx content must be pipe-table rows only (`| cell | cell |`), "
                f"got: {line!r}"
            )
        if _is_separator_row(cells):
            continue
        rows.append(cells)
    return rows


def _validate_and_normalize_formula(formula: str) -> str:
    """`formula` is a cell string starting with `=`. Raises ValueError for
    a function LibreOffice can never evaluate (see
    _XLSX_UNSUPPORTED_FUNCTIONS' comment); auto-prefixes the six functions
    Excel itself stores with a hidden `_xlfn.` prefix so the model doesn't
    have to know that OOXML wrinkle."""
    for name in _XLSX_UNSUPPORTED_FUNCTIONS:
        if re.search(rf"\b{name}\s*\(", formula, re.IGNORECASE):
            raise ValueError(
                f"Formula {formula!r} uses {name}(), which LibreOffice (used to verify "
                f"every formula this tool writes) cannot evaluate under any prefix, and "
                f"which has no spill-range metadata when openpyxl writes it -- only the "
                f"top-left cell would silently get a value. Use INDEX/MATCH for lookups; "
                f"sort, filter, and de-duplicate data in Python before writing the cells."
            )
    for name in _XLSX_XLFN_FUNCTIONS:

        def _prefix(match: re.Match[str], name: str = name) -> str:
            return f"_xlfn.{name}{match.group(1)}"

        formula = re.sub(
            rf"(?<!_xlfn\.)\b{name}(\s*\()", _prefix, formula, flags=re.IGNORECASE
        )
    return formula


def _coerce_cell(value: str) -> Any:
    if value.startswith("="):
        return _validate_and_normalize_formula(value)
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _render_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _render_sheet_table(sheet: Worksheet) -> str:
    lines = []
    for row in sheet.iter_rows():
        lines.append("| " + " | ".join(_render_cell(cell.value) for cell in row) + " |")
    return "\n".join(lines)


def _recalc_xlsx(file_path: Path, timeout: float = _XLSX_RECALC_TIMEOUT) -> dict[str, object]:
    """Force LibreOffice to recalculate every formula in `file_path` and
    rewrite it in place. Never raises -- any failure (soffice missing,
    timeout, a workbook with external file links that recalculating would
    destroy) comes back as `status: "skipped"` with a reason, the same
    graceful-degradation contract as write_pptx's overflow QA. Verified
    empirically in this sandbox: a bare `soffice --convert-to` does NOT
    recalculate (it just re-emits whatever's already cached -- nothing,
    for a file openpyxl just wrote); only the macro-based
    calculateAll()+store()+close() approach actually does.
    """
    if shutil.which("soffice") is None:
        return {"status": "skipped", "skipped_reason": "LibreOffice (soffice) not found"}

    try:
        with zipfile.ZipFile(file_path) as archive:
            has_external_links = any(
                name.startswith("xl/externalLinks/") for name in archive.namelist()
            )
    except (zipfile.BadZipFile, OSError) as exc:
        return {"status": "skipped", "skipped_reason": f"could not inspect workbook: {exc}"}
    if has_external_links:
        return {
            "status": "skipped",
            "skipped_reason": (
                "workbook links to another file -- recalculating would resolve those "
                "links' cells to #NAME? and delete the links for good, since openpyxl "
                "already stripped their cached values on save"
            ),
        }

    profile_dir = Path(tempfile.mkdtemp(prefix="coscribe_xlsx_recalc_"))
    try:
        profile_url = profile_dir.as_uri()
        try:
            subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--terminate_after_init",
                    f"-env:UserInstallation={profile_url}",
                ],
                capture_output=True,
                timeout=timeout,
                check=True,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
            return {
                "status": "skipped",
                "skipped_reason": f"could not prepare a LibreOffice profile: {exc}",
            }

        macro_dir = profile_dir / "user" / "basic" / "Standard"
        if not macro_dir.is_dir():
            return {
                "status": "skipped",
                "skipped_reason": "LibreOffice did not create a usable profile",
            }
        (macro_dir / "Module1.xba").write_text(_XLSX_RECALC_MACRO)

        try:
            subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--norestore",
                    f"-env:UserInstallation={profile_url}",
                    "vnd.sun.star.script:Standard.Module1.RecalculateAndSave"
                    "?language=Basic&location=application",
                    str(file_path),
                ],
                capture_output=True,
                timeout=timeout,
                check=True,
            )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as exc:
            return {
                "status": "skipped",
                "skipped_reason": f"LibreOffice failed to recalculate: {exc}",
            }
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)

    from openpyxl import load_workbook

    values_workbook = load_workbook(str(file_path), data_only=True)
    error_locations: dict[str, list[str]] = {}
    total_errors = 0
    for sheet_name in values_workbook.sheetnames:
        sheet = values_workbook[sheet_name]
        if not hasattr(sheet, "iter_rows"):
            continue
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    for error_string in _XLSX_ERROR_STRINGS:
                        if error_string in cell.value:
                            error_locations.setdefault(error_string, []).append(
                                f"{sheet_name}!{cell.coordinate}"
                            )
                            total_errors += 1
                            break
    values_workbook.close()

    formulas_workbook = load_workbook(str(file_path), data_only=False)
    formula_count = 0
    for sheet_name in formulas_workbook.sheetnames:
        sheet = formulas_workbook[sheet_name]
        if not hasattr(sheet, "iter_rows"):
            continue
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formula_count += 1
    formulas_workbook.close()

    return {
        "status": "success" if total_errors == 0 else "errors_found",
        "total_formulas": formula_count,
        "total_errors": total_errors,
        "error_locations": {
            error: locations[:20] for error, locations in error_locations.items()
        },
    }


def _workbook_has_any_formula(workbook: Any) -> bool:
    for sheet_name in workbook.sheetnames:
        sheet = workbook[sheet_name]
        if not hasattr(sheet, "iter_rows"):
            continue
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    return True
    return False


_CHART_TYPES = frozenset({"bar", "line", "pie"})


class SpreadsheetToolkit:
    def __init__(
        self,
        root: str | Path,
        *,
        state_dir: str | Path | None = None,
        extra_readable: Sequence[str | Path] = (),
        extra_writable: Sequence[str | Path] = (),
    ) -> None:
        self._scope = WorkspaceScope(
            root, extra_readable=extra_readable, extra_writable=extra_writable
        )
        self._state_dir = Path(state_dir) if state_dir is not None else None

    def _check_readable(self, path: str) -> Path:
        file_path = self._scope.resolve(path)
        if not file_path.exists():
            raise ValueError(f"File does not exist: {path}")
        if not file_path.is_file():
            raise ValueError(f"Path is not a file: {path}")
        return file_path

    def _check_writable(self, path: str) -> Path:
        file_path = self._scope.resolve(path, write=True)
        if file_path.exists() and file_path.is_dir():
            raise ValueError(f"Path is a directory: {path}")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path

    def read_xlsx(self, path: str, sheet: Optional[str] = None) -> str:  # noqa: UP045
        from openpyxl import load_workbook

        file_path = self._check_readable(path)
        workbook = load_workbook(str(file_path), data_only=True)
        if sheet is not None:
            if sheet not in workbook.sheetnames:
                raise ValueError(
                    f"Sheet {sheet!r} not found. Available sheets: {workbook.sheetnames}"
                )
            return _render_sheet_table(workbook[sheet])
        sections = []
        for name in workbook.sheetnames:
            sections.append(f"## {name}\n\n{_render_sheet_table(workbook[name])}")
        return "\n\n".join(sections)

    @locked_by_path
    def write_xlsx(
        self, path: str, content: str, sheet_name: str = "Sheet1", overwrite: bool = True
    ) -> dict[str, object]:
        from openpyxl import Workbook, load_workbook

        rows = _parse_table_rows(content)
        file_path = self._check_writable(path)
        if file_path.exists():
            workbook = load_workbook(str(file_path))
            if sheet_name in workbook.sheetnames:
                if not overwrite:
                    raise FileExistsError(f"Sheet already exists: {sheet_name}")
                del workbook[sheet_name]
                sheet = workbook.create_sheet(sheet_name)
            else:
                sheet = workbook.create_sheet(sheet_name)
        else:
            workbook = Workbook()
            default_sheet = workbook.active
            sheet = workbook.create_sheet(sheet_name)
            if default_sheet is not None:
                workbook.remove(default_sheet)
        for row in rows:
            sheet.append([_coerce_cell(cell) for cell in row])
        # Makes the just-written sheet the one LibreOffice renders for the
        # preview thumbnail below (and the one Excel opens to) -- without
        # this, amending an existing workbook leaves whatever sheet was
        # already active, which usually isn't sheet_name.
        workbook.active = workbook.index(sheet)
        has_formula = _workbook_has_any_formula(workbook)
        workbook.save(str(file_path))

        recalc_result: dict[str, object] = {"status": "skipped", "skipped_reason": None}
        if has_formula:
            recalc_result = _recalc_xlsx(file_path)

        preview_path, preview_skipped_reason = render_thumbnail(file_path, self._state_dir)
        return {
            "path": self._scope.relative(file_path),
            "sheet": sheet_name,
            "bytes_written": file_path.stat().st_size,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
            "recalc_status": recalc_result["status"],
            "recalc_skipped_reason": recalc_result.get("skipped_reason"),
            "total_formulas": recalc_result.get("total_formulas"),
            "total_errors": recalc_result.get("total_errors"),
            "formula_error_locations": recalc_result.get("error_locations"),
        }

    def recalc_xlsx(self, path: str, timeout: float = _XLSX_RECALC_TIMEOUT) -> dict[str, object]:
        file_path = self._check_readable(path)
        result = _recalc_xlsx(file_path, timeout=timeout)
        return {
            "path": self._scope.relative(file_path),
            "status": result["status"],
            "skipped_reason": result.get("skipped_reason"),
            "total_formulas": result.get("total_formulas"),
            "total_errors": result.get("total_errors"),
            "error_locations": result.get("error_locations"),
        }

    def format_xlsx_cells(
        self,
        path: str,
        sheet_name: str,
        cell_range: str,
        number_format: str = "",
        font_color: str = "",
        bold: bool = False,
    ) -> dict[str, object]:
        from openpyxl import load_workbook
        from openpyxl.styles import Font

        file_path = self._check_readable(path)
        workbook = load_workbook(str(file_path))
        if sheet_name not in workbook.sheetnames:
            raise ValueError(
                f"Sheet {sheet_name!r} not found. Available sheets: {workbook.sheetnames}"
            )
        sheet = workbook[sheet_name]
        try:
            selection = sheet[cell_range]
        except (ValueError, KeyError, IndexError) as exc:
            raise ValueError(f"Invalid cell_range {cell_range!r}: {exc}") from None

        # openpyxl's sheet[range] returns a bare Cell for "A1", a tuple of
        # Cells for "A1:C1", or a tuple of tuples for "A1:C3" -- normalize
        # to a flat list so the loop below doesn't need to branch on shape.
        cells: list[Any]
        if hasattr(selection, "row"):
            cells = [selection]
        else:
            cells = [cell for row in selection for cell in row]

        for cell in cells:
            if number_format:
                cell.number_format = number_format
            if font_color or bold:
                cell.font = Font(
                    name=cell.font.name,
                    size=cell.font.size,
                    bold=bold or cell.font.bold,
                    color=font_color or cell.font.color,
                )
        workbook.save(str(file_path))
        return {
            "path": self._scope.relative(file_path),
            "sheet": sheet_name,
            "cell_range": cell_range,
            "cells_formatted": len(cells),
        }

    def add_xlsx_chart(
        self,
        path: str,
        sheet_name: str,
        chart_type: str,
        data_range: str = "",
        title: str = "",
        anchor: str = "",
    ) -> dict[str, object]:
        from openpyxl import load_workbook
        from openpyxl.chart import BarChart, LineChart, PieChart, Reference
        from openpyxl.utils import get_column_letter
        from openpyxl.utils.cell import range_boundaries

        if chart_type not in _CHART_TYPES:
            raise ValueError(
                f"Unknown chart_type {chart_type!r}. Use one of: {', '.join(sorted(_CHART_TYPES))}"
            )

        file_path = self._check_readable(path)
        workbook = load_workbook(str(file_path))
        if sheet_name not in workbook.sheetnames:
            raise ValueError(
                f"Sheet {sheet_name!r} not found. Available sheets: {workbook.sheetnames}"
            )
        sheet = workbook[sheet_name]

        # Defaulting to the sheet's own used range (rather than requiring
        # the caller to compute it) sidesteps a real, live-reproduced
        # failure mode: a model asked to name the range guessed one row too
        # low and one column too wide, silently dropping the first data row
        # and charting a phantom empty series from a column that doesn't
        # exist. openpyxl already tracks the real bounds -- trust those over
        # a guess whenever the caller doesn't have a specific subset in mind.
        range_source = data_range or sheet.dimensions
        try:
            min_col, min_row, max_col, max_row = range_boundaries(range_source)
        except ValueError:
            min_col = min_row = max_col = max_row = None
        if min_col is None or min_row is None or max_col is None or max_row is None:
            raise ValueError(f"Invalid data_range: {data_range!r} -- use A1 notation, e.g. 'A1:C6'")
        if max_col <= min_col:
            raise ValueError(
                "data_range must span at least two columns -- the first is categories, "
                "the rest are data series"
            )
        if max_row <= min_row:
            raise ValueError("data_range must include a header row plus at least one data row")
        # A pie chart's slices are one series' proportions of a whole -- a
        # second series has no meaningful rendering, unlike bar/line where
        # openpyxl's add_data(titles_from_data=True) happily plots several.
        if chart_type == "pie" and max_col - min_col > 1:
            raise ValueError("pie charts take exactly one data column plus the category column")

        chart = {"bar": BarChart, "line": LineChart, "pie": PieChart}[chart_type]()
        if title:
            chart.title = title
        data = Reference(
            sheet, min_col=min_col + 1, min_row=min_row, max_col=max_col, max_row=max_row
        )
        categories = Reference(sheet, min_col=min_col, min_row=min_row + 1, max_row=max_row)
        chart.add_data(data, titles_from_data=True)
        chart.set_categories(categories)

        anchor_cell = anchor or f"{get_column_letter(max_col + 2)}{min_row}"
        # `workbook[sheet_name]` is typed as Worksheet | Chartsheet | ... --
        # mypy resolves .add_chart against Chartsheet's narrower signature.
        # We only ever index a name from workbook.sheetnames, which excludes
        # chart sheets in practice for files this package writes.
        sheet.add_chart(chart, anchor_cell)  # type: ignore[call-arg]
        workbook.save(str(file_path))

        preview_path, preview_skipped_reason = render_thumbnail(file_path, self._state_dir)
        return {
            "path": self._scope.relative(file_path),
            "sheet": sheet_name,
            "chart_type": chart_type,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }


def build_spreadsheet_tools(
    root: str | Path,
    *,
    state_dir: str | Path | None = None,
    extra_readable: Sequence[str | Path] = (),
    extra_writable: Sequence[str | Path] = (),
) -> list[Callable[..., Any]]:
    """Return the tool callables the Coordinator agent can call, bound to `root`
    (plus any user-configured extra_readable/extra_writable directories)."""
    toolkit = SpreadsheetToolkit(
        root, state_dir=state_dir, extra_readable=extra_readable, extra_writable=extra_writable
    )

    # `Optional[str]`, not `str | None`: aisuite's Tools.__infer_from_signature
    # only unwraps typing.Optional (checks `get_origin(t) is Union`), and PEP
    # 604 `X | None` has origin `types.UnionType` instead -- it slips through
    # unwrapped and gets serialized as the literal string "str | None" for
    # the "type" field, which Gemini's strict OpenAPI-subset schema rejects.
    def read_xlsx(path: str, sheet: Optional[str] = None) -> str:  # noqa: UP045
        """Read an Excel (.xlsx) file under the workspace as markdown tables.

        Without `sheet`, every sheet is rendered as a `## <sheet name>`
        heading followed by its table -- a full workbook overview in one
        call. With `sheet`, only that sheet's table is rendered (no
        heading) -- use this to avoid spending context on sheets you don't
        need in a workbook with many/large sheets.

        Args:
            path: file to read, relative to the workspace root
            sheet: sheet name to read; omit to read every sheet
        """
        return toolkit.read_xlsx(path=path, sheet=sheet)

    def write_xlsx(
        path: str, content: str, sheet_name: str = "Sheet1", overwrite: bool = True
    ) -> dict[str, object]:
        """Create or update one sheet of an Excel (.xlsx) file under the workspace.

        `content` is pipe-table rows only (`| cell | cell |`, one row per
        line) -- a spreadsheet is a grid, not flowing text, so headings/
        bullets/paragraphs don't apply here like they do for write_docx/
        write_pdf. Cells that look numeric are stored as real numbers
        (usable in Excel formulas), not text.

        A cell starting with `=` is written as a real Excel formula, e.g.
        `=SUM(B2:B9)` -- prefer this over writing a pre-computed number
        whenever the value depends on other cells, so the sheet actually
        recalculates when its inputs change. Reference other cells by
        their real address (`=B5*1.05`, not a hardcoded number); use
        `INDEX`/`MATCH` for lookups, not `XLOOKUP`, which this tool
        rejects outright (it can't be verified -- see below). If a formula
        needs to reference a cell outside this call's own `content` (e.g.
        an earlier sheet), write this sheet first, then chain another
        write_xlsx call with formulas referencing it.

        After writing, if the sheet contains any formulas, this
        automatically re-opens the file in LibreOffice to force a real
        recalculation (openpyxl itself only writes the formula text, no
        cached value) and checks for Excel error strings (`#DIV/0!` etc.)
        -- see the response's `recalc_status`/`total_errors`/
        `formula_error_locations`. `recalc_status: "errors_found"` means
        ship nothing yet: fix the formulas the errors point at and call
        write_xlsx again. A clean recalc proves the formulas *evaluate*,
        not that they're *right* -- double-check a couple of cells against
        what you expect before building out a large grid.

        Unlike write_docx/write_pdf, `overwrite` is scoped to this sheet,
        not the whole file: writing a new `sheet_name` to an existing file
        always appends it (build up a multi-sheet workbook with repeated
        calls); writing an existing `sheet_name` replaces its content only
        if `overwrite=True`, otherwise raises.

        The response's `preview_path` (when LibreOffice is installed) names
        a rendered thumbnail of `sheet_name` the user can see in the chat
        UI -- not something to fetch or parse yourself.

        Args:
            path: file to write, relative to the workspace root
            content: pipe-table rows for this sheet
            sheet_name: sheet to create or replace
            overwrite: whether to replace `sheet_name` if it already exists
        """
        return toolkit.write_xlsx(
            path=path, content=content, sheet_name=sheet_name, overwrite=overwrite
        )

    def recalc_xlsx(path: str, timeout: float = 30.0) -> dict[str, object]:
        """Force LibreOffice to recalculate every formula in an existing
        Excel (.xlsx) file and rewrite it in place with real cached values.

        write_xlsx already does this automatically whenever it writes a
        formula, so you don't need to call this after write_xlsx. Use it
        for a file that already has formulas but wasn't written by
        write_xlsx (e.g. one the user attached), or to re-verify a
        workbook you've since edited some other way.

        `status` in the response is `"success"`, `"errors_found"` (fix the
        cells `error_locations` names and recalc again), or `"skipped"`
        (LibreOffice isn't installed, or the workbook links to another
        file and recalculating would destroy that link -- see
        `skipped_reason`).

        Args:
            path: file to recalculate, relative to the workspace root
            timeout: seconds to wait for LibreOffice before giving up
        """
        return toolkit.recalc_xlsx(path=path, timeout=timeout)

    def format_xlsx_cells(
        path: str,
        sheet_name: str,
        cell_range: str,
        number_format: str = "",
        font_color: str = "",
        bold: bool = False,
    ) -> dict[str, object]:
        """Apply a number format and/or font color/weight to a cell range
        in an existing Excel (.xlsx) file.

        `cell_range` is a single cell (`"B2"`) or a range (`"B2:D10"`).
        `number_format` is an Excel format code -- common ones: currency
        `"$#,##0"`, percentage `"0.0%"` (store the value as a fraction,
        e.g. 0.15 for 15%, not 15), `"$#,##0;($#,##0);-"` for negatives in
        parentheses and zeros as a dash, `"0.0x"` for a valuation
        multiple. `font_color` is a 6-digit hex RGB string with no `#`
        (e.g. `"FF0000"` for red). For a financial model, the common
        convention is blue text for hardcoded inputs/assumptions, black
        (leave `font_color` empty) for formulas, green for links to
        another sheet -- apply it if you're building one and the user
        hasn't specified their own convention.

        Args:
            path: file to modify, relative to the workspace root
            sheet_name: sheet containing the range
            cell_range: cell or range to format, e.g. "B2" or "B2:D10"
            number_format: Excel number format code; omit to leave as-is
            font_color: 6-digit hex RGB color with no `#`; omit to leave as-is
            bold: whether to make the range bold
        """
        return toolkit.format_xlsx_cells(
            path=path,
            sheet_name=sheet_name,
            cell_range=cell_range,
            number_format=number_format,
            font_color=font_color,
            bold=bold,
        )

    def add_xlsx_chart(
        path: str,
        sheet_name: str,
        chart_type: str,
        data_range: str = "",
        title: str = "",
        anchor: str = "",
    ) -> dict[str, object]:
        """Add a chart to an existing Excel (.xlsx) file, reading data already in the sheet.

        Omit `data_range` to chart the sheet's entire used range -- the
        common case, and safer than naming it yourself: get a row or column
        wrong and the chart silently drops real data or plots a phantom
        empty series. Only pass `data_range` when you specifically want a
        subset of a larger sheet, as A1 notation covering a header row plus
        the data rows (e.g. `"A1:C6"`). Either way: the first column is the
        category axis; every other column becomes its own data series,
        named from that column's header cell. `chart_type` is `"bar"`,
        `"line"`, or `"pie"` -- a pie chart takes exactly one data column (a
        second series has no meaningful rendering as pie slices).

        The response's `preview_path` (when LibreOffice is installed) names
        a rendered thumbnail the user can see in the chat UI -- not
        something to fetch or parse yourself.

        Args:
            path: file to modify, relative to the workspace root
            sheet_name: sheet the data (and the new chart) live in
            chart_type: "bar", "line", or "pie"
            data_range: A1 range including the header row, e.g. "A1:C6";
                omit to use the sheet's entire used range
            title: optional chart title
            anchor: cell the chart's top-left corner anchors to, e.g. "E2";
                omit to place it just right of the charted range
        """
        return toolkit.add_xlsx_chart(
            path=path,
            sheet_name=sheet_name,
            chart_type=chart_type,
            data_range=data_range,
            title=title,
            anchor=anchor,
        )

    return [
        tool_metadata(read_xlsx, risk_category="READ", category="documents"),
        tool_metadata(write_xlsx, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(recalc_xlsx, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(format_xlsx_cells, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(add_xlsx_chart, risk_category="WRITE_LOCAL", category="documents"),
    ]

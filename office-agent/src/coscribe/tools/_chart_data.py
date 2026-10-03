"""Chart data shared by the tools that add a chart to a document: the
pipe-table the model writes, parsed into categories and series."""

from __future__ import annotations

from .documents import _is_separator_row, _split_table_row

CHART_TYPES = frozenset({"bar", "line", "pie"})


def parse_chart_table(content: str, tool_name: str) -> tuple[list[str], dict[str, list[float]]]:
    """Parse a pipe-table (same convention as write_xlsx's content) into
    categories (first column) and series (remaining columns, keyed by their
    header cell) -- avoids a list/dict-typed tool parameter entirely, since
    no tool in this package uses one: aisuite's Gemini schema inference has
    broken on less exotic type hints than that (see this module's and
    spreadsheets.py's `Optional[int]` comments), so a plain pipe-table
    string is the safer, already-proven shape."""
    rows: list[list[str]] = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        cells = _split_table_row(line)
        if cells is None:
            raise ValueError(
                f"{tool_name} data must be pipe-table rows only (`| cell | cell |`), "
                f"got: {line!r}"
            )
        if _is_separator_row(cells):
            continue
        rows.append(cells)

    if len(rows) < 2:
        raise ValueError(f"{tool_name} data needs a header row plus at least one data row")
    header, *data_rows = rows
    if len(header) < 2:
        raise ValueError(
            f"{tool_name} data needs a category column plus at least one data column"
        )

    series_names = header[1:]
    categories = [row[0] for row in data_rows]
    series: dict[str, list[float]] = {name: [] for name in series_names}
    for row in data_rows:
        for index, name in enumerate(series_names, start=1):
            raw_value = row[index] if index < len(row) else ""
            try:
                series[name].append(float(raw_value.replace(",", "")))
            except ValueError:
                raise ValueError(
                    f"{tool_name} data cell {raw_value!r} (column {name!r}) is not numeric"
                ) from None
    return categories, series

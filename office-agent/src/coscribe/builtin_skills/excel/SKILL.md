---
name: Excel Spreadsheets
description: Formula discipline, financial-model color/number conventions, and verification habits for Excel workbooks. Load before writing a workbook with write_xlsx.
---
Deeper conventions for `write_xlsx`/`format_xlsx_cells`/`add_xlsx_chart`/
`recalc_xlsx` -- on top of what's already in your base instructions
(pipe-table content, `=`-prefixed formulas, the per-sheet overwrite scope,
and the automatic recalc/error-check `write_xlsx` already does).

## Formulas over hardcoded values, always

If a value depends on other cells, write the formula, not the number you
computed for it. This isn't just style: a hardcoded value silently goes
stale the moment an input changes, and there's no way for the user to tell
it happened just by looking at the cell. Reference other cells by address
(`=B5*(1+$B$6)`), not by re-typing the number you read from them. Keep the
same formula shape across a whole row or column of a projection -- a
single cell that was edited by hand mid-row, breaking the pattern, is the
most common silent error in a spreadsheet and is easy to miss on a glance.

`write_xlsx` rejects `XLOOKUP`/`XMATCH`/`SORT`/`FILTER`/`UNIQUE`/`SEQUENCE`
outright because it can't verify them against LibreOffice's recalc step --
use `INDEX`/`MATCH` for lookups instead, and sort/dedupe the data in
`content` itself before writing it if you need it pre-sorted.

## Financial-model conventions

Apply these with `format_xlsx_cells` when building something in that
genre, unless the user's own file already does something different (match
an existing file's conventions over these defaults):

- Blue font (`0000FF`) for hardcoded inputs and assumptions -- anything a
  reader might reasonably want to change.
- Black (leave `font_color` empty) for formulas.
- Green (`008000`) for a formula that links to another sheet in the same
  workbook.
- Currency as `"$#,##0"`, with the unit named in the header cell
  (`Revenue ($mm)`) rather than repeated in every row.
- Percentages as `"0.0%"`, **stored as a fraction** -- `0.15` renders as
  `15.0%`; writing `15` renders as `1500.0%`, a very easy mistake to make
  since it looks right until you check the format.
- Negatives in parentheses, zeros rendered as a dash:
  `"$#,##0;($#,##0);-"`.
- Bold header rows and section labels (`bold=True`), to make a large sheet
  scannable without color alone.

## Verification is not optional

`write_xlsx` recalculates automatically and reports `recalc_status`/
`total_errors`/`formula_error_locations` whenever the sheet has formulas --
`recalc_status: "errors_found"` means don't tell the user it's done yet;
fix the cells named and write again. A clean recalc proves the formulas
*evaluate*, not that they're *correct* -- spot-check two or three values
against what you expect by hand before building out a full grid from the
same pattern, the same way you'd sanity-check the first few rows of any
generated table.

If you're editing a file the user attached rather than building one from
scratch and it already had formulas, run `recalc_xlsx` on it before
trusting any cached values you read -- `write_xlsx` only recalculates
sheets it itself just wrote.

## Structure

One assumption per labeled cell, referenced by the formulas that use it --
not a number typed directly into a formula. If a number in the sheet has
no clear source (not computed, not from the user, not an obvious input),
say in your response where it came from rather than presenting it as if it
were self-evidently correct.

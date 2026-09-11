"""Word (.docx) and PDF read/write tools, scoped to a single workspace root.

Both writers accept the same lightweight markdown subset (headings via
``#``/``##``/``###``, ``-``/``*`` bullets, ``1.`` numbered items, and
``| a | b |`` pipe tables) so the model can produce either format from one
mental model. Editing an existing document is read -> modify the text ->
write(overwrite=True), the same whole-file-overwrite contract
``write_file`` already uses.

``read_docx`` converts via ``mammoth`` (docx -> html) + ``markdownify``
(html -> markdown) rather than walking the document tree by hand -- the
same pair of well-established libraries microsoft/markitdown itself uses
for docx, without markitdown's own mandatory ``magika`` dependency (which
drags in onnxruntime + numpy for file-type sniffing this codebase doesn't
need, since each tool already knows its own format). Its output is real
markdown -- a superset of the writers' subset (arbitrary heading depth,
links, bold/italic) -- rather than an exact mirror of what the writers
accept, so round-tripping a generated file through read_docx won't be
byte-for-byte identical, just semantically equivalent. ``read_pdf`` uses
``pdfplumber`` (built on ``pdfminer.six``) for the same reason -- better
real-world text extraction than a lower-level PDF library, no hand-rolled
layout logic.

``mammoth``/``pdfplumber``/``docx``/``markdownify``/``reportlab`` are all
imported lazily, inside each method that actually needs them, rather than
at module level -- a real, measured startup-time cost (see
runtime_lg/README.md's startup-time section): every one of these tools is
registered into the Coordinator's tool list at agent-build time
regardless of whether it's ever called this session (the model needs
every tool's name/docstring/schema up front), but none of their
*implementations* are needed until a specific tool is actually invoked.
None of the tool-facing function signatures below reference any of these
libraries' own types (only `str`/`int`/`dict`/`list`), so LangChain's
tool-schema introspection never forces the lazy import open -- confirmed
by this file's own tests still passing with these deferred.

``write_docx``'s optional ``template_path`` loads an existing .docx as the
starting point instead of a blank ``Document()``, then clears its body --
keeping only ``sectPr`` (page size/margins), removing every other child --
before writing the new content. Verified empirically (round-tripped
through python-docx) that removing anything else, or removing ``sectPr``
too, breaks the document; this is the one thing in this file that isn't a
pure library call.
"""

from __future__ import annotations

import re
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

from ..runtime.types import tool_metadata
from ._thumbnail import render_thumbnail
from ._workspace import WorkspaceScope
from .files import DEFAULT_IGNORES

_HEADING_RE = re.compile(r"^(#{1,3})\s+(.*)$")
_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_NUMBER_RE = re.compile(r"^\d+\.\s+(.*)$")
_SEPARATOR_CELL_RE = re.compile(r"^:?-{2,}:?$")
_TOC_LINE_RE = re.compile(r"^\[TOC\]$", re.IGNORECASE)
_COMMENT_MARKER_RE = re.compile(r"\s*\{\{comment:\s*(.+?)\}\}\s*$", re.IGNORECASE)

_PAGE_SIZES_EMU: dict[str, tuple[int, int]] = {
    # (width, height) in EMU, portrait orientation, at python-docx's own
    # 914400-EMU-per-inch convention -- Inches()/Mm() both just multiply
    # into this unit, using raw EMU here avoids importing docx.shared at
    # module level (kept a lazy, function-local import like every other
    # `docx` usage in this file, see the module docstring).
    "letter": (7772400, 10058400),  # 8.5in x 11in
    "legal": (7772400, 12801600),  # 8.5in x 14in
    "a4": (7560310, 10692130),  # 210mm x 297mm
    "a3": (10692130, 15120620),  # 297mm x 420mm
}


def _extract_comment(text: str) -> tuple[str, str | None]:
    """Split a trailing ``{{comment: ...}}`` marker off a block's text.

    Returns ``(visible_text, comment_text_or_None)``. Only recognized on
    headings/paragraphs/bullets/numbered items -- table cells are taken
    literally, so a marker inside one is not a comment (documented v1
    scope limit, see ``write_docx``'s docstring)."""
    match = _COMMENT_MARKER_RE.search(text)
    if not match:
        return text, None
    return text[: match.start()].rstrip(), match.group(1).strip()


@dataclass
class Block:
    kind: str  # "heading" | "paragraph" | "bullet" | "number" | "table" | "toc"
    text: str = ""
    level: int = 1
    ordinal: int = 1
    rows: list[list[str]] = field(default_factory=list)
    comment: str | None = None


def _split_table_row(line: str) -> list[str] | None:
    if not (line.startswith("|") and line.endswith("|") and len(line) >= 2):
        return None
    return [cell.strip() for cell in line[1:-1].split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return all(_SEPARATOR_CELL_RE.match(cell) for cell in cells)


def parse_blocks(content: str) -> list[Block]:
    """Parse the shared markdown subset into structured blocks."""
    blocks: list[Block] = []
    paragraph_lines: list[str] = []
    table_rows: list[list[str]] = []
    number_counter = 0

    def flush_paragraph() -> None:
        text = " ".join(paragraph_lines).strip()
        if text:
            text, comment = _extract_comment(text)
            blocks.append(Block(kind="paragraph", text=text, comment=comment))
        paragraph_lines.clear()

    def flush_table() -> None:
        if table_rows:
            blocks.append(Block(kind="table", rows=[list(row) for row in table_rows]))
            table_rows.clear()

    for raw_line in content.splitlines():
        line = raw_line.strip()

        cells = _split_table_row(line)
        if cells is not None:
            flush_paragraph()
            if not _is_separator_row(cells):
                table_rows.append(cells)
            continue
        flush_table()

        if not line:
            flush_paragraph()
            number_counter = 0
            continue

        if _TOC_LINE_RE.match(line):
            flush_paragraph()
            number_counter = 0
            blocks.append(Block(kind="toc"))
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush_paragraph()
            number_counter = 0
            level = min(len(heading_match.group(1)), 3)
            text, comment = _extract_comment(heading_match.group(2).strip())
            blocks.append(Block(kind="heading", level=level, text=text, comment=comment))
            continue

        bullet_match = _BULLET_RE.match(line)
        if bullet_match:
            flush_paragraph()
            number_counter = 0
            text, comment = _extract_comment(bullet_match.group(1).strip())
            blocks.append(Block(kind="bullet", text=text, comment=comment))
            continue

        number_match = _NUMBER_RE.match(line)
        if number_match:
            flush_paragraph()
            number_counter += 1
            text, comment = _extract_comment(number_match.group(1).strip())
            blocks.append(
                Block(kind="number", text=text, ordinal=number_counter, comment=comment)
            )
            continue

        number_counter = 0
        paragraph_lines.append(line)

    flush_paragraph()
    flush_table()
    return blocks


_INLINE_MARKUP_RE = re.compile(r"\*\*(?P<bold>.+?)\*\*|\*(?P<italic1>.+?)\*|_(?P<italic2>.+?)_")


def parse_inline_runs(text: str) -> list[tuple[str, bool, bool]]:
    """Split ``**bold**``/``*italic*``/``_italic_`` markers out of a block's
    text into ``(text, bold, italic)`` runs, so a writer can style each
    fragment with its own run instead of emitting the literal asterisks/
    underscores as visible text -- the model reaches for this markdown
    convention constantly even though ``parse_blocks`` above never taught it
    to the writers, so it used to come through as literal ``**bold**``
    characters in generated docx/pptx. A lone, unterminated marker (e.g. a
    bullet reading "the file *.txt") is left as plain text rather than
    swallowed as unterminated markup, since the regex only matches when a
    closing marker is also present."""
    runs: list[tuple[str, bool, bool]] = []
    pos = 0
    for match in _INLINE_MARKUP_RE.finditer(text):
        if match.start() > pos:
            runs.append((text[pos : match.start()], False, False))
        if match.group("bold") is not None:
            runs.append((match.group("bold"), True, False))
        else:
            italic_text = match.group("italic1")
            if italic_text is None:
                italic_text = match.group("italic2")
            runs.append((italic_text, False, True))
        pos = match.end()
    if pos < len(text):
        runs.append((text[pos:], False, False))
    return runs or [(text, False, False)]


def _add_inline_runs(paragraph: Any, text: str) -> None:
    """Add ``text`` to a python-docx paragraph as one run per
    ``parse_inline_runs`` fragment, so ``**bold**``/``*italic*`` render as
    real formatting instead of literal markup characters."""
    for run_text, bold, italic in parse_inline_runs(text):
        run = paragraph.add_run(run_text)
        if bold:
            run.bold = True
        if italic:
            run.italic = True


def _clear_body(document: Any) -> None:
    """Remove every existing paragraph/table/etc. from a loaded template's
    body, keeping only ``sectPr`` (section properties: page size, margins)
    -- verified empirically that removing it too breaks the document, and
    that removing everything else and re-adding new content afterward
    round-trips cleanly through python-docx."""
    for child in list(document.element.body):
        if not child.tag.endswith("}sectPr"):
            document.element.body.remove(child)


def _mark_body_deleted(document: Any, author: str, change_ids: _ChangeIdCounter) -> None:
    """Template-editing + ``track_changes=True`` variant of ``_clear_body``:
    instead of silently discarding the template's existing paragraphs,
    wrap each one's runs in ``<w:del>``/``<w:delText>`` and mark its
    paragraph mark deleted too, so Word shows the old content as a
    reviewable tracked deletion rather than making it vanish outright.
    Keeps ``sectPr`` like ``_clear_body``. Tables in the template are still
    removed outright (documented v1 scope limit -- table-level tracked
    deletion needs cell/row markup this tool doesn't generate)."""
    for child in list(document.element.body):
        if child.tag.endswith("}sectPr"):
            continue
        if child.tag.endswith("}p"):
            _mark_paragraph_deleted(child, author, change_ids)
        else:
            document.element.body.remove(child)


def _add_toc(document: Any) -> Any:
    """Insert a Word TOC field (``TOC \\o "1-3" \\h \\z \\u``) at the current
    end of the document -- python-docx has no native TOC API, this is the
    standard raw-OXML field-code recipe (fldChar begin/separate/end +
    instrText), matching the docx skill's own guidance that headings must
    use built-in ``HeadingLevel``-equivalent styles (``add_heading`` already
    does that). The placeholder text only shows until Word recalculates the
    field -- ``_enable_update_fields`` makes that happen automatically on
    open instead of requiring the user to press F9."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    paragraph = document.add_paragraph()
    run = paragraph.add_run()
    r_element = run._r

    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")

    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = 'TOC \\o "1-3" \\h \\z \\u'

    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")

    placeholder = OxmlElement("w:t")
    placeholder.text = "Right-click and choose Update Field to generate the table of contents."

    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")

    r_element.append(begin)
    r_element.append(instr)
    r_element.append(separate)
    r_element.append(placeholder)
    r_element.append(end)
    return paragraph


def _enable_update_fields(document: Any) -> None:
    """Set ``<w:updateFields w:val="true"/>`` in settings.xml so Word
    recalculates every field (our TOC included) automatically on open,
    instead of showing stale placeholder text until the user manually
    updates it."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    settings = document.settings.element
    if settings.find(qn("w:updateFields")) is not None:
        return
    element = OxmlElement("w:updateFields")
    element.set(qn("w:val"), "true")
    settings.append(element)


def _apply_page_setup(document: Any, page_size: str, orientation: str) -> None:
    """Set page width/height/orientation on every section -- python-docx
    exposes this natively (``section.page_width``/``page_height``/
    ``orientation``), it's just never been wired into ``write_docx`` before
    now. Empty ``page_size``/``orientation`` leaves that axis at whatever
    the blank document or template already has."""
    from docx.enum.section import WD_ORIENT
    from docx.shared import Emu

    if page_size:
        key = page_size.strip().lower()
        if key not in _PAGE_SIZES_EMU:
            raise ValueError(
                f"Unknown page_size {page_size!r}. Supported: "
                f"{', '.join(sorted(_PAGE_SIZES_EMU))}"
            )
    if orientation:
        orientation_key = orientation.strip().lower()
        if orientation_key not in ("portrait", "landscape"):
            raise ValueError(
                f"Unknown orientation {orientation!r}. Use 'portrait' or 'landscape'."
            )
    else:
        orientation_key = ""

    for section in document.sections:
        width, height = (
            _PAGE_SIZES_EMU[page_size.strip().lower()]
            if page_size
            else (section.page_width, section.page_height)
        )
        if orientation_key == "landscape":
            width, height = max(width, height), min(width, height)
            section.orientation = WD_ORIENT.LANDSCAPE
        elif orientation_key == "portrait":
            width, height = min(width, height), max(width, height)
            section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Emu(int(width))
        section.page_height = Emu(int(height))


class _ChangeIdCounter:
    """Shared, monotonically increasing ``w:id`` source for every
    ``<w:ins>``/``<w:del>`` element a single ``write_docx`` call produces --
    Word requires these unique across the whole document."""

    def __init__(self) -> None:
        self._next = 0

    def next(self) -> int:
        value = self._next
        self._next += 1
        return value


def _mark_paragraph_deleted(
    paragraph_element: Any, author: str, change_ids: _ChangeIdCounter
) -> None:
    """In place: convert every run's ``<w:t>`` to ``<w:delText>``, wrap each
    run in ``<w:del>``, and mark the paragraph mark itself deleted (so
    accepting the change merges this paragraph into the next one, matching
    Word's own behavior for a fully-deleted paragraph and the docx skill's
    documented XML shape for paragraph deletions)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    date = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    p_pr = paragraph_element.find(qn("w:pPr"))
    if p_pr is None:
        p_pr = OxmlElement("w:pPr")
        paragraph_element.insert(0, p_pr)
    r_pr = p_pr.find(qn("w:rPr"))
    if r_pr is None:
        r_pr = OxmlElement("w:rPr")
        p_pr.append(r_pr)
    mark_del = OxmlElement("w:del")
    mark_del.set(qn("w:id"), str(change_ids.next()))
    mark_del.set(qn("w:author"), author)
    mark_del.set(qn("w:date"), date)
    r_pr.insert(0, mark_del)

    for run in list(paragraph_element.findall(qn("w:r"))):
        texts = run.findall(qn("w:t"))
        if not texts:
            continue
        for text_element in texts:
            text_element.tag = qn("w:delText")
            text_element.set(qn("xml:space"), "preserve")
        run_index = list(paragraph_element).index(run)
        wrapper = OxmlElement("w:del")
        wrapper.set(qn("w:id"), str(change_ids.next()))
        wrapper.set(qn("w:author"), author)
        wrapper.set(qn("w:date"), date)
        paragraph_element.remove(run)
        wrapper.append(run)
        paragraph_element.insert(run_index, wrapper)


def _mark_paragraph_inserted(
    paragraph_element: Any, author: str, change_ids: _ChangeIdCounter
) -> None:
    """In place: wrap every direct ``<w:r>`` run child in ``<w:ins>``, so
    Word shows this (newly written) paragraph's text as a tracked insertion
    a reviewer can accept/reject, instead of silently-already-final
    content. Tables are not wrapped this way (documented v1 scope limit --
    see ``write_docx``'s docstring)."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    date = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for run in list(paragraph_element.findall(qn("w:r"))):
        run_index = list(paragraph_element).index(run)
        wrapper = OxmlElement("w:ins")
        wrapper.set(qn("w:id"), str(change_ids.next()))
        wrapper.set(qn("w:author"), author)
        wrapper.set(qn("w:date"), date)
        paragraph_element.remove(run)
        wrapper.append(run)
        paragraph_element.insert(run_index, wrapper)


def _new_comment_anchor(paragraph_element: Any, comment_id: str) -> None:
    """Wrap the whole paragraph in ``<w:commentRangeStart>``/
    ``<w:commentRangeEnd>`` + append the ``<w:commentReference>`` run, so
    the comment (written separately to ``comments.xml``) anchors to this
    entire paragraph/bullet/heading. No sub-paragraph anchor in this tool's
    v1 -- a comment always spans the whole block it's attached to."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    start = OxmlElement("w:commentRangeStart")
    start.set(qn("w:id"), comment_id)
    p_pr = paragraph_element.find(qn("w:pPr"))
    paragraph_element.insert(1 if p_pr is not None else 0, start)

    end = OxmlElement("w:commentRangeEnd")
    end.set(qn("w:id"), comment_id)
    paragraph_element.append(end)

    ref_run = OxmlElement("w:r")
    r_pr = OxmlElement("w:rPr")
    r_style = OxmlElement("w:rStyle")
    r_style.set(qn("w:val"), "CommentReference")
    r_pr.append(r_style)
    ref_run.append(r_pr)
    comment_ref = OxmlElement("w:commentReference")
    comment_ref.set(qn("w:id"), comment_id)
    ref_run.append(comment_ref)
    paragraph_element.append(ref_run)


def _build_comments_xml(comments: list[tuple[str, str, str]]) -> bytes:
    """``comments``: list of ``(comment_id, author, text)``. Builds the
    standalone ``word/comments.xml`` part -- small and flat enough that
    plain string templating (with proper XML escaping) is simpler and just
    as safe here as a full XML-tree build."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
    ]
    for comment_id, author, text in comments:
        initials = "".join(part[0] for part in author.split()[:2]).upper() or "?"
        parts.append(
            f'<w:comment w:id="{comment_id}" w:author="{_xml_escape(author)}" '
            f'w:date="{now}" w:initials="{_xml_escape(initials)}">'
            f"<w:p><w:r><w:t xml:space=\"preserve\">{_xml_escape(text)}</w:t></w:r></w:p>"
            f"</w:comment>"
        )
    parts.append("</w:comments>")
    return "".join(parts).encode("utf-8")


def _write_comments_part(file_path: Path, comments_xml: bytes) -> None:
    """Append ``word/comments.xml`` + its content-type override + a
    relationship to an already-saved .docx. python-docx has no public API
    for adding a part type it doesn't itself know about, so this is the
    same "unzip, patch, rezip" approach the docx skill documents for adding
    comments to an existing file -- applied here right after this tool's
    own from-scratch save. ``word/document.xml`` itself is never re-parsed
    here: python-docx already wrote the comment-range anchors correctly as
    part of the normal save, since ``_new_comment_anchor`` mutated the
    in-memory tree before ``document.save()`` was called."""
    from lxml import etree

    with zipfile.ZipFile(file_path, "r") as zin:
        entries = {name: zin.read(name) for name in zin.namelist()}

    entries["word/comments.xml"] = comments_xml

    ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    content_types = etree.fromstring(entries["[Content_Types].xml"])
    override = etree.SubElement(content_types, f"{{{ct_ns}}}Override")
    override.set("PartName", "/word/comments.xml")
    override.set(
        "ContentType",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml",
    )
    entries["[Content_Types].xml"] = etree.tostring(
        content_types, xml_declaration=True, encoding="UTF-8", standalone=True
    )

    pr_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    rels_path = "word/_rels/document.xml.rels"
    rels_root = etree.fromstring(entries[rels_path])
    max_n = 0
    for rel in rels_root.findall(f"{{{pr_ns}}}Relationship"):
        rid = rel.get("Id") or ""
        if rid.startswith("rId") and rid[3:].isdigit():
            max_n = max(max_n, int(rid[3:]))
    relationship = etree.SubElement(rels_root, f"{{{pr_ns}}}Relationship")
    relationship.set("Id", f"rId{max_n + 1}")
    relationship.set(
        "Type", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
    )
    relationship.set("Target", "comments.xml")
    entries[rels_path] = etree.tostring(
        rels_root, xml_declaration=True, encoding="UTF-8", standalone=True
    )

    with zipfile.ZipFile(file_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name, payload in entries.items():
            zout.writestr(name, payload)


class DocumentToolkit:
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

    def _check_writable(self, path: str, overwrite: bool) -> Path:
        file_path = self._scope.resolve(path, write=True)
        if file_path.exists() and file_path.is_dir():
            raise ValueError(f"Path is a directory: {path}")
        if file_path.exists() and not overwrite:
            raise FileExistsError(f"File already exists: {path}")
        file_path.parent.mkdir(parents=True, exist_ok=True)
        return file_path

    def read_docx(self, path: str) -> str:
        import mammoth
        from markdownify import markdownify

        file_path = self._check_readable(path)
        with file_path.open("rb") as docx_file:
            html = mammoth.convert_to_html(docx_file).value
        return markdownify(
            html, heading_style="ATX", bullets="-", table_infer_header=True
        ).strip()

    def write_docx(
        self,
        path: str,
        content: str,
        overwrite: bool = True,
        template_path: str = "",
        page_size: str = "",
        orientation: str = "",
        track_changes: bool = False,
        change_author: str = "Coscribe",
        comment_author: str = "Coscribe",
    ) -> dict[str, object]:
        from docx import Document

        file_path = self._check_writable(path, overwrite)
        change_ids = _ChangeIdCounter()
        if template_path:
            template_file = self._check_readable(template_path)
            document = Document(str(template_file))
            if track_changes:
                _mark_body_deleted(document, change_author, change_ids)
            else:
                _clear_body(document)
        else:
            document = Document()

        has_toc = False
        pending_comments: list[tuple[Any, str]] = []
        for block in parse_blocks(content):
            paragraph = None
            if block.kind == "heading":
                paragraph = document.add_heading("", level=block.level)
                _add_inline_runs(paragraph, block.text)
            elif block.kind == "bullet":
                paragraph = document.add_paragraph(style="List Bullet")
                _add_inline_runs(paragraph, block.text)
            elif block.kind == "number":
                paragraph = document.add_paragraph(style="List Number")
                _add_inline_runs(paragraph, block.text)
            elif block.kind == "toc":
                paragraph = _add_toc(document)
                has_toc = True
            elif block.kind == "table" and block.rows:
                n_cols = max(len(row) for row in block.rows)
                table = document.add_table(rows=len(block.rows), cols=n_cols)
                table.style = "Table Grid"
                for row_index, row in enumerate(block.rows):
                    for col_index in range(n_cols):
                        cell_text = row[col_index] if col_index < len(row) else ""
                        _add_inline_runs(table.cell(row_index, col_index).paragraphs[0], cell_text)
            else:
                paragraph = document.add_paragraph()
                _add_inline_runs(paragraph, block.text)

            if paragraph is None:
                continue
            if track_changes:
                _mark_paragraph_inserted(paragraph._p, change_author, change_ids)
            if block.comment:
                pending_comments.append((paragraph._p, block.comment))

        if has_toc:
            _enable_update_fields(document)
        if page_size or orientation:
            _apply_page_setup(document, page_size, orientation)
        for index, (paragraph_element, _) in enumerate(pending_comments):
            _new_comment_anchor(paragraph_element, str(index))

        document.save(str(file_path))

        if pending_comments:
            comments_xml = _build_comments_xml(
                [
                    (str(index), comment_author, text)
                    for index, (_, text) in enumerate(pending_comments)
                ]
            )
            _write_comments_part(file_path, comments_xml)

        preview_path, preview_skipped_reason = render_thumbnail(file_path, self._state_dir)
        return {
            "path": self._scope.relative(file_path),
            "bytes_written": file_path.stat().st_size,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
            "has_toc": has_toc,
            "comments_added": len(pending_comments),
            "tracked_changes": track_changes,
        }

    def read_pdf(self, path: str) -> str:
        import pdfplumber

        file_path = self._check_readable(path)
        pages = []
        with pdfplumber.open(str(file_path)) as pdf:
            for index, page in enumerate(pdf.pages, start=1):
                text = (page.extract_text() or "").strip()
                pages.append(f"--- Page {index} ---\n{text}")
        return "\n\n".join(pages)

    def search_pdf(
        self, query: str, path: str = ".", pattern: str = "*.pdf"
    ) -> list[dict[str, object]]:
        import pdfplumber

        base = self._scope.resolve(path)
        if not base.exists():
            raise ValueError(f"Path does not exist: {path}")
        matches: list[dict[str, object]] = []
        for item in sorted(base.rglob(pattern)):
            if not item.is_file():
                continue
            relative_parts = item.relative_to(self._scope.root).parts
            if any(part in DEFAULT_IGNORES for part in relative_parts):
                continue
            with pdfplumber.open(str(item)) as pdf:
                for page_number, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    for line in text.splitlines():
                        if query in line:
                            matches.append(
                                {
                                    "path": self._scope.relative(item),
                                    "page": page_number,
                                    "text": line,
                                }
                            )
                        if len(matches) >= 100:
                            return matches
        return matches

    def write_pdf(self, path: str, content: str, overwrite: bool = True) -> dict[str, object]:
        from reportlab.lib import colors
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import (
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        file_path = self._check_writable(path, overwrite)
        styles = getSampleStyleSheet()
        bullet_style = ParagraphStyle("CoscribeBullet", parent=styles["Normal"], leftIndent=18)

        flowables: list[Any] = []
        for block in parse_blocks(content):
            if block.kind == "heading":
                heading_style = styles[f"Heading{block.level}"]
                flowables.append(Paragraph(_xml_escape(block.text), heading_style))
            elif block.kind == "bullet":
                # Plain ASCII, not "•" -- reportlab's default font maps
                # the unicode bullet glyph in a way pdfplumber/pypdf both
                # mis-render on read-back (a stray "(cid:127)" or a dropped
                # character), confirmed while testing the write/read round trip.
                flowables.append(Paragraph(f"- {_xml_escape(block.text)}", bullet_style))
            elif block.kind == "number":
                numbered_text = f"{block.ordinal}. {_xml_escape(block.text)}"
                flowables.append(Paragraph(numbered_text, bullet_style))
            elif block.kind == "table" and block.rows:
                table = Table(block.rows)
                table.setStyle(
                    TableStyle(
                        [
                            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                        ]
                    )
                )
                flowables.append(table)
            else:
                flowables.append(Paragraph(_xml_escape(block.text), styles["Normal"]))
            flowables.append(Spacer(1, 6))

        SimpleDocTemplate(str(file_path)).build(flowables)
        preview_path, preview_skipped_reason = render_thumbnail(file_path, self._state_dir)
        return {
            "path": self._scope.relative(file_path),
            "bytes_written": file_path.stat().st_size,
            "preview_path": preview_path,
            "preview_skipped_reason": preview_skipped_reason,
        }


def build_document_tools(
    root: str | Path,
    *,
    state_dir: str | Path | None = None,
    extra_readable: Sequence[str | Path] = (),
    extra_writable: Sequence[str | Path] = (),
) -> list[Callable[..., Any]]:
    """Return the tool callables the Coordinator agent can call, bound to `root`
    (plus any user-configured extra_readable/extra_writable directories)."""
    toolkit = DocumentToolkit(
        root, state_dir=state_dir, extra_readable=extra_readable, extra_writable=extra_writable
    )

    def read_docx(path: str) -> str:
        """Read a Word (.docx) file under the workspace as markdown.

        Headings, lists, tables, links, and bold/italic all come through as
        their markdown equivalents.

        Args:
            path: file to read, relative to the workspace root
        """
        return toolkit.read_docx(path=path)

    def write_docx(
        path: str,
        content: str,
        overwrite: bool = True,
        template_path: str = "",
        page_size: str = "",
        orientation: str = "",
        track_changes: bool = False,
        change_author: str = "Coscribe",
        comment_author: str = "Coscribe",
    ) -> dict[str, object]:
        """Create a Word (.docx) file under the workspace from structured text.

        `content` uses a light markdown subset: `#`/`##`/`###` for headings,
        `-`/`*` for bullet items, `1.` for numbered items, and
        `| cell | cell |` rows for tables. Plain lines become paragraphs.
        Two extra markers on top of that subset:

        - A line that is exactly `[TOC]` inserts a real Word table-of-
          contents field at that position, built from the document's own
          heading levels. It shows placeholder text until Word recalculates
          it, which happens automatically the first time the file is opened
          -- no manual "Update Field" needed.
        - A trailing `{{comment: your comment text}}` on a heading,
          paragraph, bullet, or numbered-item line attaches a real Word
          comment (visible in Word's Comments pane) anchored to that whole
          line, authored as `comment_author`. Not recognized inside table
          cells.

        The response's `preview_path` (when LibreOffice is installed) names
        a rendered thumbnail of the first page the user can see in the
        chat UI -- not something to fetch or parse yourself.

        `template_path`, if given, is an existing .docx file (e.g. one with
        the user's own letterhead/styles/page setup) to build the new
        document on: its styles and page setup (size, margins) carry over.
        Without `track_changes`, its own body content is removed before the
        new content from `content` is added. With `track_changes=True`, the
        template's existing paragraphs are kept but marked as a tracked
        deletion instead (visible to the user as a reviewable redline, not
        silently discarded) -- tables in the template are still removed
        outright either way.

        `page_size` ("letter", "legal", "a4", or "a3") and `orientation`
        ("portrait" or "landscape") override the blank document's or
        template's page setup; leave either empty to keep the default.

        `track_changes=True` wraps every paragraph/bullet/heading this call
        writes in a tracked insertion (`w:ins`, authored as `change_author`)
        instead of writing it as already-final content -- use this when the
        document should go to a human as a reviewable redline they can
        accept/reject in Word, rather than a silent rewrite. Tables are
        always written as final content, not tracked, even with
        `track_changes=True` (documented scope limit -- table-level tracked
        changes need row/cell markup this tool doesn't generate).

        Args:
            path: file to write, relative to the workspace root
            content: document content in the markdown subset described above
            overwrite: whether to replace the file if it already exists
            template_path: optional .docx file to use as the document's
                starting styles/page-setup template, relative to the
                workspace root
            page_size: "letter", "legal", "a4", "a3", or empty to leave
                the current page size unchanged
            orientation: "portrait", "landscape", or empty to leave the
                current orientation unchanged
            track_changes: write this call's content as tracked
                insertions (and, with template_path, mark the template's
                old content as a tracked deletion instead of removing it)
            change_author: author name recorded on tracked-change markup
                when track_changes is True
            comment_author: author name recorded on `{{comment: ...}}`
                markers
        """
        return toolkit.write_docx(
            path=path,
            content=content,
            overwrite=overwrite,
            template_path=template_path,
            page_size=page_size,
            orientation=orientation,
            track_changes=track_changes,
            change_author=change_author,
            comment_author=comment_author,
        )

    def read_pdf(path: str) -> str:
        """Extract the text contents of a PDF file under the workspace, page by page.

        Args:
            path: file to read, relative to the workspace root
        """
        return toolkit.read_pdf(path=path)

    def write_pdf(path: str, content: str, overwrite: bool = True) -> dict[str, object]:
        """Create a PDF file under the workspace from structured text.

        Uses the same markdown subset as `write_docx` (headings, bullets,
        numbered items, pipe tables, and plain paragraphs).

        The response's `preview_path` (when LibreOffice is installed) names
        a rendered thumbnail of the first page the user can see in the
        chat UI -- not something to fetch or parse yourself.

        Args:
            path: file to write, relative to the workspace root
            content: document content in the markdown subset described above
            overwrite: whether to replace the file if it already exists
        """
        return toolkit.write_pdf(path=path, content=content, overwrite=overwrite)

    def search_pdf(
        query: str, path: str = ".", pattern: str = "*.pdf"
    ) -> list[dict[str, object]]:
        """Search for a literal substring across PDF files under the workspace.

        Finds matches without needing to read_pdf an entire (possibly large)
        document into context first.

        Args:
            query: substring to search for
            path: directory to search under, relative to the workspace root
            pattern: glob pattern to filter PDF files by, e.g. "report*.pdf"
        """
        return toolkit.search_pdf(query=query, path=path, pattern=pattern)

    return [
        tool_metadata(read_docx, risk_category="READ", category="documents"),
        tool_metadata(write_docx, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(read_pdf, risk_category="READ", category="documents"),
        tool_metadata(write_pdf, risk_category="WRITE_LOCAL", category="documents"),
        tool_metadata(search_pdf, risk_category="READ", category="documents"),
    ]

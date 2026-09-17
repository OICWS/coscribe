import shutil
import tempfile
from pathlib import Path

import pytest
from docx import Document
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

from coscribe.runtime.types import get_tool_metadata
from coscribe.tools.documents import DocumentToolkit, build_document_tools, parse_inline_runs


def _tools_by_name(root: Path) -> dict[str, object]:
    return {tool.__name__: tool for tool in build_document_tools(root)}  # type: ignore[attr-defined]


def _libreoffice_actually_works() -> bool:
    """Same reasoning as test_presentations_tool.py's identical helper --
    `shutil.which("soffice")` alone doesn't prove conversion actually
    works in this environment."""
    probe_dir = Path(tempfile.mkdtemp(prefix="coscribe_lo_probe_"))
    try:
        result = DocumentToolkit(probe_dir, state_dir=probe_dir / "state").write_pdf(
            path="probe.pdf", content="Probe"
        )
        return result["preview_skipped_reason"] is None
    finally:
        shutil.rmtree(probe_dir, ignore_errors=True)


DOCUMENT_CONTENT = """\
# Report

Some intro paragraph.

## Findings

- first point
- second point

| name | score |
| --- | --- |
| Alice | 9 |
| Bob | 7 |
"""


def test_write_docx_then_read_docx_round_trips_structure(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="report.docx", content=DOCUMENT_CONTENT)

    text = tools["read_docx"](path="report.docx")

    assert "# Report" in text
    assert "## Findings" in text
    assert "Some intro paragraph." in text
    assert "- first point" in text
    assert "- second point" in text
    assert "| Alice | 9 |" in text
    assert "| Bob | 7 |" in text


def test_parse_inline_runs_splits_bold_and_italic_markers() -> None:
    assert parse_inline_runs("plain text") == [("plain text", False, False)]
    assert parse_inline_runs("**bold**") == [("bold", True, False)]
    assert parse_inline_runs("*italic*") == [("italic", False, True)]
    assert parse_inline_runs("_also italic_") == [("also italic", False, True)]
    assert parse_inline_runs("a **b** c *d* e") == [
        ("a ", False, False),
        ("b", True, False),
        (" c ", False, False),
        ("d", False, True),
        (" e", False, False),
    ]


def test_parse_inline_runs_leaves_an_unterminated_marker_as_plain_text() -> None:
    # A lone asterisk with no closing partner (e.g. "the file *.txt") isn't
    # markup -- it should pass through unchanged, not swallow the rest of
    # the string as unterminated italics.
    assert parse_inline_runs("the file *.txt is here") == [
        ("the file *.txt is here", False, False)
    ]


def test_write_docx_bold_and_italic_markers_become_real_formatting(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_docx"](
        path="formatted.docx",
        content="This is **bold** and *italic* and plain text.",
    )

    document = Document(tmp_path / "formatted.docx")
    paragraph = document.paragraphs[0]
    run_texts = [run.text for run in paragraph.runs]

    assert "".join(run_texts) == "This is bold and italic and plain text."
    assert not any("*" in text for text in run_texts)
    bold_runs = [run for run in paragraph.runs if run.bold]
    italic_runs = [run for run in paragraph.runs if run.italic]
    assert [run.text for run in bold_runs] == ["bold"]
    assert [run.text for run in italic_runs] == ["italic"]

    # And it round-trips back through read_docx as real markdown, not
    # literal asterisks.
    text = tools["read_docx"](path="formatted.docx")
    assert "**bold**" in text


def test_read_docx_surfaces_run_level_formatting_not_just_paragraph_text(
    tmp_path: Path,
) -> None:
    # A doc built directly with python-docx (not through write_docx) with
    # run-level bold and a "List Number" style paragraph -- content a naive
    # paragraph.text walk would flatten away but mammoth/markdownify (what
    # read_docx is built on) should surface as real markdown.
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("plain ")
    paragraph.add_run("bold").bold = True
    document.add_paragraph("first", style="List Number")
    document.add_paragraph("second", style="List Number")
    document.save(tmp_path / "formatted.docx")

    text = DocumentToolkit(tmp_path).read_docx("formatted.docx")

    assert "**bold**" in text
    assert "1. first" in text
    assert "2. second" in text


def test_write_pdf_then_read_pdf_recovers_text_content(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_pdf"](path="report.pdf", content=DOCUMENT_CONTENT)

    text = tools["read_pdf"](path="report.pdf")

    assert "Report" in text
    assert "Findings" in text
    assert "first point" in text
    assert "second point" in text
    assert "Alice" in text
    assert "Bob" in text


def test_write_docx_does_not_overwrite_when_disabled(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="a.docx", content="# Original")

    with pytest.raises(FileExistsError):
        tools["write_docx"](path="a.docx", content="# Replacement", overwrite=False)

    assert "Original" in tools["read_docx"](path="a.docx")


def test_write_pdf_does_not_overwrite_when_disabled(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_pdf"](path="a.pdf", content="Original")

    with pytest.raises(FileExistsError):
        tools["write_pdf"](path="a.pdf", content="Replacement", overwrite=False)

    assert "Original" in tools["read_pdf"](path="a.pdf")


def test_write_docx_with_template_path_keeps_page_setup_and_drops_old_content(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="template.docx", content="# Old Title\n- old bullet")

    template = Document(tmp_path / "template.docx")
    template.sections[0].page_width = 5486400  # A5 width, in EMU
    template.save(tmp_path / "template.docx")

    tools["write_docx"](
        path="new.docx", content="# New Title\n- new bullet", template_path="template.docx"
    )

    text = tools["read_docx"](path="new.docx")
    assert "New Title" in text
    assert "new bullet" in text
    assert "Old Title" not in text
    assert "old bullet" not in text

    new_doc = Document(tmp_path / "new.docx")
    assert new_doc.sections[0].page_width == 5486400


def test_write_docx_with_missing_template_path_raises(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(ValueError, match="does not exist"):
        tools["write_docx"](path="new.docx", content="# Hi", template_path="missing.docx")


def test_write_docx_toc_marker_inserts_a_toc_field_and_enables_update_fields(
    tmp_path: Path,
) -> None:
    from docx.oxml.ns import qn

    tools = _tools_by_name(tmp_path)
    result = tools["write_docx"](
        path="report.docx", content="# Title\n\n[TOC]\n\n## Section\n\nBody text."
    )
    assert result["has_toc"] is True

    doc = Document(tmp_path / "report.docx")
    instr_texts = [
        el.text for el in doc.element.body.iter(qn("w:instrText")) if el.text
    ]
    assert any("TOC" in text for text in instr_texts)

    update_fields = doc.settings.element.find(qn("w:updateFields"))
    assert update_fields is not None
    assert update_fields.get(qn("w:val")) == "true"


def test_write_docx_without_toc_marker_reports_no_toc(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    result = tools["write_docx"](path="report.docx", content="# Title\n\nBody.")
    assert result["has_toc"] is False


def test_write_docx_comment_marker_creates_a_word_comment_and_strips_the_marker(
    tmp_path: Path,
) -> None:
    import zipfile

    from lxml import etree

    tools = _tools_by_name(tmp_path)
    result = tools["write_docx"](
        path="report.docx",
        content="# Title\n\nSome text here. {{comment: please verify this}}\n",
        comment_author="Reviewer Bot",
    )
    assert result["comments_added"] == 1

    text = tools["read_docx"](path="report.docx")
    assert "{{comment:" not in text
    assert "Some text here." in text

    with zipfile.ZipFile(tmp_path / "report.docx") as archive:
        comments_xml = archive.read("word/comments.xml")
        content_types = archive.read("[Content_Types].xml")
        rels = archive.read("word/_rels/document.xml.rels")

    w_ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    comments_root = etree.fromstring(comments_xml)
    comments = comments_root.findall(f"{{{w_ns}}}comment")
    assert len(comments) == 1
    assert comments[0].get(f"{{{w_ns}}}author") == "Reviewer Bot"
    comment_text = comments[0].find(f".//{{{w_ns}}}t")
    assert comment_text is not None
    assert comment_text.text == "please verify this"

    assert b"/word/comments.xml" in content_types
    assert b"relationships/comments" in rels


def test_write_docx_without_comment_marker_reports_zero_comments(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    result = tools["write_docx"](path="report.docx", content="# Title\n\nPlain text.")
    assert result["comments_added"] == 0


def test_write_docx_page_size_and_orientation_are_applied(tmp_path: Path) -> None:
    from docx.enum.section import WD_ORIENT

    tools = _tools_by_name(tmp_path)
    tools["write_docx"](
        path="report.docx", content="# Title", page_size="a4", orientation="landscape"
    )

    doc = Document(tmp_path / "report.docx")
    section = doc.sections[0]
    assert section.orientation == WD_ORIENT.LANDSCAPE
    assert section.page_width > section.page_height  # wider than tall once landscape


def test_write_docx_rejects_unknown_page_size(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    with pytest.raises(ValueError, match="Unknown page_size"):
        tools["write_docx"](path="report.docx", content="# Title", page_size="tabloid")


def test_write_docx_rejects_unknown_orientation(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    with pytest.raises(ValueError, match="Unknown orientation"):
        tools["write_docx"](path="report.docx", content="# Title", orientation="sideways")


def test_write_docx_track_changes_wraps_new_content_in_tracked_insertions(
    tmp_path: Path,
) -> None:
    from docx.oxml.ns import qn

    tools = _tools_by_name(tmp_path)
    result = tools["write_docx"](
        path="report.docx",
        content="# New Title\n\nNew paragraph.",
        track_changes=True,
        change_author="AI Draft",
    )
    assert result["tracked_changes"] is True

    doc = Document(tmp_path / "report.docx")
    ins_elements = doc.element.body.findall(qn("w:p") + "/" + qn("w:ins"))
    assert len(ins_elements) >= 2
    assert all(el.get(qn("w:author")) == "AI Draft" for el in ins_elements)

    # Still readable as normal text -- tracked insertions aren't deletions.
    text = tools["read_docx"](path="report.docx")
    assert "New Title" in text
    assert "New paragraph." in text


def test_write_docx_track_changes_with_template_marks_old_content_deleted(
    tmp_path: Path,
) -> None:
    from docx.oxml.ns import qn

    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="template.docx", content="# Old Title\n\nOld paragraph.")

    tools["write_docx"](
        path="redline.docx",
        content="# New Title\n\nNew paragraph.",
        template_path="template.docx",
        track_changes=True,
        change_author="AI Draft",
    )

    doc = Document(tmp_path / "redline.docx")
    del_text_elements = doc.element.body.iter(qn("w:delText"))
    deleted_texts = {el.text for el in del_text_elements}
    assert deleted_texts == {"Old Title", "Old paragraph."}

    ins_run_texts = {
        text_element.text
        for ins_element in doc.element.body.iter(qn("w:ins"))
        for text_element in ins_element.iter(qn("w:t"))
    }
    assert ins_run_texts == {"New Title", "New paragraph."}


_CONTRACT_CONTENT = (
    "# Report\n\n"
    "The total price is 100 dollars, due within 30 days of the invoice date.\n\n"
    "A second unrelated paragraph about widgets.\n"
)


def test_insert_docx_tracked_text_wraps_the_insertion_in_a_real_w_ins(tmp_path: Path) -> None:
    from docx.oxml.ns import qn

    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="c.docx", content=_CONTRACT_CONTENT)

    result = tools["insert_docx_tracked_text"](
        path="c.docx", text=" (extended)", context_before="30 days", author="Alice"
    )
    assert result["type"] == "insertion"
    assert result["author"] == "Alice"

    doc = Document(tmp_path / "c.docx")
    ins_elements = list(doc.element.body.iter(qn("w:ins")))
    assert len(ins_elements) == 1
    assert ins_elements[0].get(qn("w:author")) == "Alice"
    assert "".join(t.text or "" for t in ins_elements[0].iter(qn("w:t"))) == " (extended)"
    # The inserted text is invisible from python-docx's own paragraph.text
    # (it walks bare <w:r> children, not <w:ins>) until accepted -- this is
    # exactly the "reviewable, not silently final" property tracked
    # insertion exists for.
    assert " (extended)" not in doc.paragraphs[1].text


def test_delete_docx_tracked_text_wraps_the_deletion_in_a_real_w_del(tmp_path: Path) -> None:
    from docx.oxml.ns import qn

    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="c.docx", content=_CONTRACT_CONTENT)

    result = tools["delete_docx_tracked_text"](path="c.docx", text="100 dollars", author="Bob")
    assert result["type"] == "deletion"

    doc = Document(tmp_path / "c.docx")
    del_elements = list(doc.element.body.iter(qn("w:del")))
    assert len(del_elements) == 1
    assert del_elements[0].get(qn("w:author")) == "Bob"
    assert (
        "".join(t.text or "" for t in del_elements[0].iter(qn("w:delText"))) == "100 dollars"
    )
    # Still present as a <w:delText>, not actually removed -- the text is
    # gone from python-docx's own paragraph.text (which skips w:del) but
    # not from the file itself.
    assert "100 dollars" not in doc.paragraphs[1].text


def test_replace_docx_tracked_text_marks_only_the_changed_portion(tmp_path: Path) -> None:
    from docx.oxml.ns import qn

    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="c.docx", content=_CONTRACT_CONTENT)

    result = tools["replace_docx_tracked_text"](path="c.docx", find="widgets", replace="gadgets")
    assert result["type"] == "replacement"

    doc = Document(tmp_path / "c.docx")
    deleted = {
        t.text for el in doc.element.body.iter(qn("w:del")) for t in el.iter(qn("w:delText"))
    }
    inserted = {t.text for el in doc.element.body.iter(qn("w:ins")) for t in el.iter(qn("w:t"))}
    assert deleted == {"widgets"}
    assert inserted == {"gadgets"}


def test_accept_docx_tracked_changes_makes_insertions_permanent_and_removes_deletions(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="c.docx", content=_CONTRACT_CONTENT)
    tools["insert_docx_tracked_text"](path="c.docx", text=" (extended)", context_before="30 days")
    tools["delete_docx_tracked_text"](path="c.docx", text="100 dollars")

    result = tools["accept_docx_tracked_changes"](path="c.docx")
    assert result["accepted"] == 2
    assert result["scope"] == "all"

    doc = Document(tmp_path / "c.docx")
    body_text = doc.paragraphs[1].text
    assert "100 dollars" not in body_text
    assert "30 days (extended)" in body_text


def test_reject_docx_tracked_changes_restores_the_original_text(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="c.docx", content=_CONTRACT_CONTENT)
    tools["insert_docx_tracked_text"](path="c.docx", text=" (extended)", context_before="30 days")
    tools["delete_docx_tracked_text"](path="c.docx", text="100 dollars")

    result = tools["reject_docx_tracked_changes"](path="c.docx")
    assert result["rejected"] == 2

    doc = Document(tmp_path / "c.docx")
    assert doc.paragraphs[1].text == (
        "The total price is 100 dollars, due within 30 days of the invoice date."
    )


def test_accept_docx_tracked_changes_can_be_scoped_to_one_author(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="c.docx", content=_CONTRACT_CONTENT)
    tools["insert_docx_tracked_text"](
        path="c.docx", text=" ALICE", context_before="30 days", author="Alice"
    )
    tools["insert_docx_tracked_text"](
        path="c.docx", text=" BOB", context_before="invoice date", author="Bob"
    )

    result = tools["accept_docx_tracked_changes"](path="c.docx", author="Alice")
    assert result["accepted"] == 1
    assert result["scope"] == "by_author"

    doc = Document(tmp_path / "c.docx")
    from docx.oxml.ns import qn

    remaining_ins_authors = {
        el.get(qn("w:author")) for el in doc.element.body.iter(qn("w:ins"))
    }
    assert remaining_ins_authors == {"Bob"}
    assert " ALICE" in doc.paragraphs[1].text


def test_insert_docx_tracked_text_requires_context_before(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="c.docx", content=_CONTRACT_CONTENT)

    with pytest.raises(ValueError, match="context_before is required"):
        tools["insert_docx_tracked_text"](path="c.docx", text="x", context_before="")


def test_delete_docx_tracked_text_raises_when_text_not_found(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="c.docx", content=_CONTRACT_CONTENT)

    with pytest.raises(ValueError, match="not found"):
        tools["delete_docx_tracked_text"](path="c.docx", text="nonexistent phrase")


def test_delete_docx_tracked_text_raises_when_ambiguous_across_paragraphs(
    tmp_path: Path,
) -> None:
    tools = _tools_by_name(tmp_path)
    tools["write_docx"](
        path="c.docx",
        content="Alpha repeated text here.\n\nBeta repeated text here too.\n",
    )

    with pytest.raises(ValueError, match="different paragraphs"):
        tools["delete_docx_tracked_text"](path="c.docx", text="repeated text")


def test_tracked_edit_tools_still_validate_against_wml_xsd(tmp_path: Path) -> None:
    # A real end-to-end confirmation that the tracked-change writers go
    # through the same validation gate write_docx does -- not just that
    # they produce *some* w:ins/w:del, but that the whole resulting tree
    # stays a schema-conformant document.
    from coscribe.tools._ooxml_validate import _WML_XSD, ooxml_errors

    tools = _tools_by_name(tmp_path)
    tools["write_docx"](path="c.docx", content=_CONTRACT_CONTENT)
    tools["insert_docx_tracked_text"](path="c.docx", text=" (extended)", context_before="30 days")
    tools["delete_docx_tracked_text"](path="c.docx", text="100 dollars")
    tools["replace_docx_tracked_text"](path="c.docx", find="widgets", replace="gadgets")

    doc = Document(tmp_path / "c.docx")
    assert ooxml_errors(doc.element, schema_path=_WML_XSD) == []


@pytest.mark.real_libreoffice
@pytest.mark.skipif(not _libreoffice_actually_works(), reason="LibreOffice not usable here")
def test_write_docx_with_all_advanced_features_converts_cleanly_via_libreoffice(
    tmp_path: Path,
) -> None:
    """Real, non-mocked round trip: TOC + comment + page setup + tracked
    changes all in one document, converted through actual LibreOffice --
    catches OOXML corruption that a pure-Python structural check can miss."""
    state_dir = tmp_path / "state"
    tools = {
        tool.__name__: tool
        for tool in build_document_tools(tmp_path / "workspace", state_dir=state_dir)  # type: ignore[attr-defined]
    }
    tools["write_docx"](path="template.docx", content="# Draft\n\nOriginal content.")

    result = tools["write_docx"](
        path="final.docx",
        content=(
            "# Final Report\n\n[TOC]\n\n## Notes\n\n"
            "Reviewed content. {{comment: looks good}}\n"
        ),
        template_path="template.docx",
        page_size="a4",
        orientation="landscape",
        track_changes=True,
        change_author="AI Draft",
        comment_author="Reviewer",
    )
    assert result["has_toc"] is True
    assert result["comments_added"] == 1
    assert result["tracked_changes"] is True
    assert result["preview_skipped_reason"] is None
    assert result["preview_path"] is not None


def test_document_paths_cannot_escape_workspace_root(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(PermissionError):
        tools["read_docx"](path="../outside.docx")
    with pytest.raises(PermissionError):
        tools["write_docx"](path="../outside.docx", content="# Hi")
    with pytest.raises(PermissionError):
        tools["read_pdf"](path="../outside.pdf")
    with pytest.raises(PermissionError):
        tools["write_pdf"](path="../outside.pdf", content="Hi")


def test_read_tools_are_low_risk_write_tools_require_approval(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    for name in ("read_docx", "read_pdf"):
        metadata = get_tool_metadata(tools[name])
        assert metadata.risk_category == "READ"
        assert metadata.requires_approval is False

    for name in (
        "write_docx",
        "write_pdf",
        "insert_docx_tracked_text",
        "delete_docx_tracked_text",
        "replace_docx_tracked_text",
        "accept_docx_tracked_changes",
        "reject_docx_tracked_changes",
    ):
        metadata = get_tool_metadata(tools[name])
        assert metadata.risk_category == "WRITE_LOCAL"
        assert metadata.requires_approval is True

    search_metadata = get_tool_metadata(tools["search_pdf"])
    assert search_metadata.risk_category == "READ"
    assert search_metadata.requires_approval is False


def _write_two_page_pdf(path: Path, page_one: str, page_two: str) -> None:
    styles = getSampleStyleSheet()
    SimpleDocTemplate(str(path)).build(
        [
            Paragraph(page_one, styles["Normal"]),
            PageBreak(),
            Paragraph(page_two, styles["Normal"]),
        ]
    )


def test_search_pdf_finds_match_with_correct_page_number(tmp_path: Path) -> None:
    _write_two_page_pdf(
        tmp_path / "report.pdf", "nothing relevant here", "the quarterly total was 42"
    )
    tools = _tools_by_name(tmp_path)

    results = tools["search_pdf"](query="quarterly total")

    assert len(results) == 1
    assert results[0]["path"] == "report.pdf"
    assert results[0]["page"] == 2
    assert "quarterly total" in results[0]["text"]


def test_search_pdf_scans_multiple_files_and_reports_the_matching_one(tmp_path: Path) -> None:
    _write_two_page_pdf(tmp_path / "a.pdf", "apples", "oranges")
    _write_two_page_pdf(tmp_path / "b.pdf", "bananas", "the target phrase is here")
    tools = _tools_by_name(tmp_path)

    results = tools["search_pdf"](query="target phrase")

    assert [r["path"] for r in results] == ["b.pdf"]


def test_search_pdf_returns_empty_list_when_nothing_matches(tmp_path: Path) -> None:
    _write_two_page_pdf(tmp_path / "report.pdf", "one", "two")
    tools = _tools_by_name(tmp_path)

    assert tools["search_pdf"](query="not present anywhere") == []


def test_search_pdf_path_cannot_escape_workspace_root(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)

    with pytest.raises(PermissionError):
        tools["search_pdf"](query="x", path="../outside")


def test_write_docx_and_write_pdf_skip_preview_without_state_dir(tmp_path: Path) -> None:
    tools = _tools_by_name(tmp_path)  # no state_dir passed

    docx_result = tools["write_docx"](path="report.docx", content="# Title")
    pdf_result = tools["write_pdf"](path="report.pdf", content="Title")

    for result in (docx_result, pdf_result):
        assert result["preview_path"] is None
        assert result["preview_skipped_reason"] == "no state_dir configured"


@pytest.mark.real_libreoffice
@pytest.mark.skipif(
    not _libreoffice_actually_works(),
    reason="LibreOffice not installed or not functional in this environment",
)
def test_write_docx_and_write_pdf_generate_real_previews_via_libreoffice(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    tools = {
        tool.__name__: tool
        for tool in build_document_tools(tmp_path / "workspace", state_dir=state_dir)  # type: ignore[attr-defined]
    }

    docx_result = tools["write_docx"](path="report.docx", content="# Title")
    pdf_result = tools["write_pdf"](path="report.pdf", content="Title")

    for result in (docx_result, pdf_result):
        assert result["preview_skipped_reason"] is None
        preview_path = state_dir / "previews" / result["preview_path"]
        assert preview_path.is_file()
        assert preview_path.stat().st_size > 0


def test_extra_writable_dir_lets_write_docx_land_outside_workspace(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    workspace = tmp_path / "workspace"
    tools = {
        tool.__name__: tool
        for tool in build_document_tools(workspace, extra_writable=[shared])  # type: ignore[attr-defined]
    }

    tools["write_docx"](path=str(shared / "report.docx"), content=DOCUMENT_CONTENT)

    assert "# Report" in tools["read_docx"](path=str(shared / "report.docx"))


def test_extra_readable_dir_rejects_write_docx(tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    workspace = tmp_path / "workspace"
    tools = {
        tool.__name__: tool
        for tool in build_document_tools(workspace, extra_readable=[downloads])  # type: ignore[attr-defined]
    }

    with pytest.raises(PermissionError):
        tools["write_docx"](path=str(downloads / "report.docx"), content=DOCUMENT_CONTENT)
